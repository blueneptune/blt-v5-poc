"""`--probe`: try each Commvault call blt depends on, once, and say what
came back.

For first contact with a CommServe that is not known to behave like the
ones blt has been run against - Commvault Cloud (SaaS), a different
version, or a token whose user has a narrower role. It answers "which
parts of blt will work here?" in about twenty requests, stores nothing,
and changes nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from commvault_sdk import CommvaultClient
from loguru import logger
from sdk_primer import SDKError

# How many clients to look through for instances, and for a SQL Server
# instance to try the database calls on. Kept small: this is a probe.
_CLIENTS_TO_TRY = 10
_CLIENTS_TO_SEARCH = 40


def run_probe(cv: CommvaultClient) -> bool:
    """True if everything job collection needs worked."""
    results: list[tuple[str, str, bool, str]] = []
    state: dict[str, Any] = {}

    def attempt(needed_for: str, name: str, call: Callable[[], str]) -> bool:
        try:
            detail = call()
        except SDKError as exc:
            results.append((needed_for, name, False, f"{type(exc).__name__}: {exc}"[:150]))
            return False
        except Exception as exc:  # a shape blt did not expect is a finding too
            results.append(
                (needed_for, name, False, f"unexpected answer - {type(exc).__name__}: {exc}"[:150])
            )
            return False
        results.append((needed_for, name, True, detail))
        return True

    def info() -> str:
        data = cv.commcell.info()
        return f"{data.get('hostName')} version {data.get('csVersionInfo')}"

    def listing() -> str:
        total, jobs = cv.jobs.sample(86_400)
        state["job"] = jobs[0].job_id if jobs else None
        statuses = sorted({job.status for job in jobs})
        return f"{total} jobs in the last 24 h; statuses seen: {', '.join(statuses) or 'none'}"

    def time_slice() -> str:
        now = datetime.now(UTC)
        total, _ = cv.jobs.sample(
            30 * 86_400, ended_between=(now - timedelta(days=2), now - timedelta(days=1))
        )
        return f"{total} jobs ended between 48 and 24 h ago"

    def oldest() -> str:
        return f"oldest finished job started {cv.jobs.oldest_start(3650 * 86_400)}"

    def one_job() -> str:
        if not state.get("job"):
            return "skipped - no job to look up"
        job = cv.jobs.get(state["job"])
        return f"job {state['job']}: {job.status if job else 'not found'}"

    def details() -> str:
        if not state.get("job"):
            return "skipped - no job to look up"
        return f"job {state['job']}: {cv.jobs.counts(state['job'])}"

    def clients() -> str:
        state["clients"] = cv.clients.list()
        return f"{len(state['clients'])} clients"

    def agents_and_instances() -> str:
        found: dict[str, int] = {}
        looked = 0
        for client in state.get("clients", [])[:_CLIENTS_TO_SEARCH]:
            # Summarise the first few; after that keep going only to find
            # a SQL Server instance for the next step, and stop once found.
            if looked >= _CLIENTS_TO_TRY and "sql" in state:
                break
            for instance in cv.instances.list(client["clientId"]):
                app = instance.get("appName", "?")
                if looked < _CLIENTS_TO_TRY:
                    found[app] = found.get(app, 0) + 1
                if app == "SQL Server" and "sql" not in state:
                    state["sql"] = instance
            if "agents" not in state:
                state["agents"] = cv.clients.agents(client["clientId"])
            looked += 1
        state["looked"] = looked
        summary = ", ".join(f"{count} {app}" for app, count in sorted(found.items()))
        return f"first {min(looked, _CLIENTS_TO_TRY)} clients: {summary or 'no instances'}"

    def sql() -> str:
        instance = state.get("sql")
        if instance is None:
            return (
                f"skipped - no SQL Server instance among the first {state.get('looked', 0)} clients"
            )
        discovered = 0
        for subclient in cv.subclients.list(instance["clientId"]):
            if subclient.get("instanceId") == instance["instanceId"]:
                discovered += len(cv.sql.discovered(cv.subclients.get(subclient["subclientId"])))
        backed_up = cv.sql.databases(instance["instanceId"])
        return (
            f"{instance.get('instanceName')}: {discovered} databases discovered, "
            f"{len(backed_up)} with a backup on record"
        )

    # Commvault Cloud refuses this to tenant and MSP users, and nothing
    # blt does on SaaS needs it; trying it there only reports a failure
    # that is not one.
    if cv.deployment != "saas":
        attempt("--check", "CommServe info", info)
    jobs_ok = attempt("job collection", "job listing", listing)
    attempt("job collection", "one job by id", one_job)
    attempt("--backfill", "job listing by end-time range", time_slice)
    attempt("--backfill", "oldest job", oldest)
    attempt("--report", "job details", details)
    if attempt("--inventory", "client listing", clients):
        attempt("--inventory", "agents and instances", agents_and_instances)
        attempt("--inventory, --report", "SQL Server databases", sql)

    width = max(len(name) for _, name, _, _ in results)
    for needed_for, name, ok, detail in results:
        logger.log(
            "INFO" if ok else "ERROR",
            "{} {:<{w}}  {}  [{}]",
            "ok    " if ok else "FAILED",
            name,
            detail,
            needed_for,
            w=width,
        )
    failed = [name for _, name, ok, _ in results if not ok]
    used = f" ({cv.budget.used} requests counted)" if cv.budget else ""
    if failed:
        logger.error("Probe: {} of {} calls failed{}", len(failed), len(results), used)
    else:
        logger.info("Probe: all {} calls worked{}", len(results), used)
    return jobs_ok
