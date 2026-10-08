"""`blt-lookup`: one job, as Commvault has it right now, next to what blt
has stored for it.

For the question "is blt wrong about this job, or just behind?". It asks
the CommServe for the job's current summary, asks the blt API for its
stored row, and prints the two side by side with what differs. Both
sides are only read; nothing is changed anywhere unless --refresh is
given, which stores the live copy in blt the same way a collection run
would.

    blt-lookup --commcell prod-east --job 123456
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sdk_primer import APIClient, NotFoundError, SDKError, configure_logging

from blt.commvault import CommvaultClient, TokenSet
from blt.schemas import JobIn, JobOut, classify_status

from .settings import commcell_env_path, load_settings
from .store import BltStore
from .tokens import env_token_saver

# (label, field on JobIn/JobOut). Compared in this order.
FIELDS: list[tuple[str, str]] = [
    ("status", "status"),
    ("percent complete", "percent_complete"),
    ("elapsed seconds", "elapsed_seconds"),
    ("start time", "start_time"),
    ("end time", "end_time"),
    ("last update time", "last_update_time"),
    ("job type", "job_type"),
    ("operation", "operation"),
    ("backup level", "backup_level"),
    ("client", "client_name"),
    ("subclient", "subclient_name"),
    ("size of application", "size_of_application"),
    ("files", "total_files"),
    ("failed files", "failed_files"),
    ("pending reason", "pending_reason"),
]


# Fields GET /Job/{id} does not return although the job listing does.
_ABSENT_FROM_SINGLE_JOB = {"last_update_time"}


def _show(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    text = str(value).replace("\n", " ")
    return text if len(text) <= 46 else text[:43] + "..."


def compare(live: JobIn | None, stored: JobOut | None) -> list[tuple[str, str, str, bool]]:
    """(label, Commvault's value, blt's value, same?) for each field."""
    rows = []
    for label, name in FIELDS:
        a = getattr(live, name, None) if live else None
        b = getattr(stored, name, None) if stored else None
        if name in _ABSENT_FROM_SINGLE_JOB and live is not None and a is None:
            # Commvault's one-job reply leaves this out; the listing blt
            # collects from includes it. Not a difference.
            rows.append((label, "(not in a single-job reply)", _show(b), True))
            continue
        rows.append((label, _show(a), _show(b), a == b))
    return rows


def explain(live: JobIn | None, stored: JobOut | None, now: datetime) -> str:
    """One sentence on what the comparison means."""
    if live is None and stored is None:
        return "Neither the CommServe nor blt knows this job id."
    if stored is None:
        return (
            "The CommServe has this job and blt does not. It has not been collected yet - "
            "it may be older than history has been backfilled to."
        )
    age = now - stored.last_collected_at
    behind = (
        f"blt last collected it {_age(age.total_seconds())} ago (run {stored.last_seen_run_id})"
    )
    if live is None:
        return f"blt has this job and the CommServe no longer does; {behind}."
    differing = [label for label, _, _, same in compare(live, stored) if not same]
    if not differing:
        return f"blt matches the CommServe; {behind}."
    _, active = classify_status(live.status)
    if active:
        return (
            f"The job is still active, so its numbers move between collections; {behind}. "
            "If that is longer than your collection interval, collection is not running "
            "or not succeeding - check the collection_run table."
        )
    return (
        f"The job has finished and blt differs in: {', '.join(differing)}; {behind}. "
        "The next collection, or --refresh, will bring it up to date."
    )


def _age(seconds: float) -> str:
    if seconds < 120:
        return f"{int(seconds)} seconds"
    if seconds < 7200:
        return f"{int(seconds // 60)} minutes"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} hours"
    return f"{seconds / 86400:.1f} days"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="blt-lookup",
        description="Compare one job as Commvault has it now with what blt has stored.",
    )
    parser.add_argument("--commcell", required=True, help="names config/<commcell>.env")
    parser.add_argument("--job", required=True, type=int, help="the Commvault job id")
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="also store the CommServe's current copy in blt",
    )
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()

    configure_logging(level=args.log_level.upper())
    try:
        settings = load_settings(args.commcell, args.config_dir)
    except Exception as exc:
        print(f"Configuration problem for {args.commcell}: {exc}", file=sys.stderr)
        sys.exit(78)

    tokens = None
    if settings.cv_access_token:
        tokens = TokenSet(
            settings.cv_access_token.get_secret_value(),
            settings.cv_refresh_token.get_secret_value() if settings.cv_refresh_token else None,
            settings.cv_token_expires_at,
            settings.cv_token_renewable_until,
        )
    try:
        with (
            CommvaultClient(
                settings.cv_base_url,
                settings.cv_username,
                settings.cv_password.get_secret_value() if settings.cv_password else None,
                access_token=tokens,
                on_token_renew=env_token_saver(commcell_env_path(args.commcell, args.config_dir)),
                verify_tls=settings.cv_verify_tls,
                ca_bundle=settings.cv_ca_bundle,
                timeout=settings.cv_timeout_seconds,
            ) as commvault,
            APIClient(
                base_url=settings.blt_api_url,
                default_headers={"X-API-Key": settings.blt_api_key.get_secret_value()},
            ) as api,
        ):
            now = datetime.now(UTC)
            live = commvault.jobs.get(args.job)
            try:
                stored: JobOut | None = JobOut.model_validate(
                    api.get(f"/commcells/{args.commcell}/jobs/{args.job}").json()
                )
            except NotFoundError:
                stored = None

            print(f"Job {args.job} on {args.commcell}, compared at {_show(now)}\n")
            rows = compare(live, stored)
            width = max(len(label) for label, *_ in rows)
            print(f"  {'':<{width}}  {'COMMVAULT NOW':<46}  {'BLT DATABASE':<46}")
            for label, a, b, same in rows:
                print(f"{' ' if same else '*'} {label:<{width}}  {a:<46}  {b:<46}")
            if stored is not None:
                print(
                    f"\n  blt: state={stored.state}, active={stored.is_active}, "
                    f"first seen {_show(stored.first_seen_at)}, "
                    f"last collected {_show(stored.last_collected_at)}"
                )
            print("\n" + explain(live, stored, now))

            if args.refresh and live is not None:
                BltStore(api, args.commcell).upsert_jobs(None, now, [live])
                print("Stored the CommServe's current copy in blt (--refresh).")
    except SDKError as exc:
        logger.error("{}", exc)
        print(f"Lookup failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
