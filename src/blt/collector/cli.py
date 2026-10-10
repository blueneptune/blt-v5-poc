"""`blt-collect` - what collect.sh runs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger
from sdk_primer import APIClient, ClientError, NotFoundError, SDKError, configure_logging

from blt.schemas import PROBLEM_VERDICTS, InstanceReportRow, ValidationReport

from .collect import run_backfill, run_collection, run_inventory
from .connect import commvault_client
from .inventory import iter_inventory
from .lock import run_lock
from .probe import run_probe
from .settings import load_settings
from .source import CommvaultJobs
from .store import BltStore


def print_instance_report(rows: list[InstanceReportRow]) -> int:
    """One line per SQL Server instance: its latest full backup against
    the databases known on it. Returns how many lines are a problem."""
    header = ("CLIENT", "INSTANCE", "FULL JOB", "STATUS", "DISCOVERED", "BACKED UP", "NOTES")
    table = [
        (
            row.client or "-",
            row.instance,
            str(row.job_id) if row.job_id else "-",
            row.job_status or "-",
            str(row.discovered),
            str(row.backed_up),
            row.notes,
        )
        for row in rows
    ]
    widths = [max(len(line[i]) for line in [header, *table]) for i in range(6)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths) + "  {}"
    logger.info(fmt, *header)
    problems = 0
    for row, line in zip(rows, table, strict=True):
        bad = bool(row.missed) or row.backed_up < row.discovered or not row.job_id
        problems += bad
        logger.log("ERROR" if bad else "INFO", fmt, *line)
    if not rows:
        logger.info("No SQL Server instances in the inventory - run --inventory first.")
    return problems


def print_validation(report: ValidationReport) -> None:
    logger.info(
        "Validation for {} - sources: {}, inventory taken {}",
        report.commcell,
        ", ".join(report.sources),
        report.inventory_at or "NEVER (run --inventory first)",
    )
    for row in report.rows:
        where = "/".join(part for part in (row.client, row.instance) if part)
        job = ""
        if row.last_backup_job_id:
            job = (
                f" [job {row.last_backup_job_id}: {row.last_backup_job_status or 'not collected'}]"
            )
        logger.log(
            "ERROR" if row.verdict in PROBLEM_VERDICTS else "INFO",
            "{:<12} {:<9} {:<28} {}{}",
            row.verdict,
            row.kind,
            f"{where}/{row.name}" if where else row.name,
            row.detail,
            job,
        )
    summary = ", ".join(f"{count} {verdict}" for verdict, count in sorted(report.counts.items()))
    logger.log(
        "ERROR" if report.problems else "INFO",
        "{}: {} - {}",
        report.commcell,
        summary or "nothing to validate",
        f"{report.problems} PROBLEM(S)" if report.problems else "validation passed",
    )
    logger.info(
        "Judged from the backup product's own inventory: this finds what it knows about "
        "and has not protected, not what it has never seen."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="blt-collect", description="Collect Commvault job history into the blt API."
    )
    parser.add_argument("--commcell", required=True, help="names config/<commcell>.env")
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument(
        "--full",
        action="store_true",
        help="ignore the watermark and collect the whole history in one query",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="collect older history, a bounded batch of time slices per run",
    )
    parser.add_argument(
        "--inventory",
        action="store_true",
        help="collect what exists (clients, instances, databases) instead of jobs",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="report what the stored inventory shows as unprotected; exit 2 if anything is",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="per SQL instance: databases discovered vs backed up by its latest full; "
        "exit 2 if any were missed",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="--report: every stored row (one per instance per full backup) instead",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=24,
        help="--validate: a backup older than this counts as stale",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="try each Commvault call blt uses, once, and report which work; stores nothing",
    )
    parser.add_argument(
        "--partner-accounts",
        action="store_true",
        help="MSP on Commvault Cloud: list the customer accounts this login can reach",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="only verify the CommCell credentials work; collect nothing",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    configure_logging(level=args.log_level.upper())

    # Runs that write take a lock, so a scheduled run that overlaps the
    # previous one of the same kind exits instead of piling on. Each kind
    # has its own, so a long backfill never holds up regular collection.
    # The read-only modes need none.
    if args.check or args.probe or args.partner_accounts or args.validate or args.report:
        _run(args)
        return
    kind = "backfill" if args.backfill else "inventory" if args.inventory else "collection"
    with run_lock(args.config_dir.resolve().parent / ".locks", f"{args.commcell}.{kind}") as held:
        if not held:
            logger.warning("A {} for {} is already running, skipping.", kind, args.commcell)
            sys.exit(75)
        _run(args)


def _run(args: argparse.Namespace) -> None:
    try:
        settings = load_settings(args.commcell, args.config_dir)
    except Exception as exc:  # missing file or missing/invalid setting
        logger.error("Configuration problem for {}: {}", args.commcell, exc)
        sys.exit(78)

    commvault = commvault_client(settings, args.commcell, args.config_dir)

    if args.probe:
        with commvault:
            sys.exit(0 if run_probe(commvault) else 1)

    if args.partner_accounts:
        # For an MSP: the customer accounts this login can reach, to find
        # the value for CV_ACCOUNT_ID.
        try:
            with commvault:
                accounts = commvault.partner.accounts()
        except SDKError as exc:
            logger.error("Could not list partner accounts: {}", exc)
            sys.exit(1)
        for account in accounts:
            shown = {
                key: account[key]
                for key in ("id", "accountId", "lhAccountId", "name", "tenantGuid", "status")
                if key in account
            }
            logger.info("{}", shown or account)
        logger.info("{} accounts. The id is what goes in CV_ACCOUNT_ID.", len(accounts))
        return

    if args.check:
        try:
            with commvault:
                try:
                    info = commvault.commcell.info()
                    described = (
                        f"CommServe {info.get('hostName')} version {info.get('csVersionInfo')}"
                    )
                except (ClientError, NotFoundError):
                    # Not every user may read the CommServe's own details
                    # (a tenant on Commvault Cloud, for one). Being able to
                    # list jobs proves the credentials just as well.
                    total, _ = commvault.jobs.sample(86_400, limit=1)
                    described = f"{total} jobs visible in the last 24 hours"
        except SDKError as exc:
            logger.error("Could not authenticate to {}: {}", args.commcell, exc)
            sys.exit(1)
        logger.info("Authenticated to {}: {}", args.commcell, described)
        return

    try:
        with (
            commvault,
            APIClient(
                base_url=settings.blt_api_url,
                default_headers={"X-API-Key": settings.blt_api_key.get_secret_value()},
                timeout=settings.cv_timeout_seconds,
            ) as api,
        ):
            store = BltStore(api, args.commcell)
            if args.report:
                problems = print_instance_report(store.instance_report(args.history))
                sys.exit(2 if problems and not args.history else 0)
            elif args.validate:
                # Reads only what is already stored; Commvault is not contacted.
                report = store.validation(args.max_age_hours)
                print_validation(report)
                sys.exit(2 if report.problems else 0)
            elif args.inventory:
                skipped: list[str] = []
                inventory = run_inventory(
                    lambda: iter_inventory(commvault, skipped), store, problems=skipped
                )
                if inventory.status != "succeeded":
                    sys.exit(1)
            elif args.backfill:
                run_backfill(
                    CommvaultJobs(commvault),
                    store,
                    chunk_hours=settings.cv_backfill_chunk_hours,
                    max_chunks=settings.cv_backfill_max_chunks,
                    history_limit_days=settings.cv_history_limit_days,
                    pause_seconds=settings.cv_backfill_pause_seconds,
                )
            else:
                run_collection(
                    CommvaultJobs(commvault),
                    store,
                    initial_lookback_hours=settings.cv_initial_lookback_hours,
                    history_limit_days=settings.cv_history_limit_days,
                    overlap_minutes=settings.cv_overlap_minutes,
                    force_full=args.full,
                )
    except (SDKError, RuntimeError) as exc:
        logger.error("Collection for {} failed: {}", args.commcell, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
