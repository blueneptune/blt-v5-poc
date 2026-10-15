"""The collection modes as Prefect flows.

Each does what the matching collect.sh mode does, with the same code;
Prefect adds the schedule, retries, and a record of every run with its
log. Settings and credentials come from blocks (blocks.py) instead of
.env files.

- collect_jobs     - every CommCell: active jobs and what finished since
                     the last run (collect.sh <commcell>). Hourly.
- collect_inventory- every CommCell: clients, instances, databases
                     (collect.sh <commcell> --inventory). Twice a day.
- backfill         - one CommCell's older history (--backfill). By hand;
                     once a CommCell's history is in, it is not needed.

The "every CommCell" flows run one child flow per CommCell, so one
CommCell failing - or retrying - does not hold up the others, and the
parent fails at the end if any did.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from commvault_sdk import CommvaultClient
from loguru import logger
from prefect import flow, get_run_logger
from prefect.client.orchestration import get_client
from prefect.client.schemas.objects import State
from sdk_primer import APIClient

from blt.collector.collect import run_backfill, run_collection, run_inventory
from blt.collector.connect import build_client
from blt.collector.inventory import iter_inventory
from blt.collector.lock import run_lock
from blt.collector.settings import CollectorSettings
from blt.collector.source import CommvaultJobs
from blt.collector.store import BltStore

from .blocks import BLOCK_TYPE_SLUG, BlockTokenStore, settings_from_blocks

LOCKS = Path(tempfile.gettempdir()) / "blt-locks"
Kind = Literal["collection", "inventory", "backfill"]


@contextmanager
def logs_to_prefect() -> Iterator[None]:
    """blt logs with loguru; send those lines to the flow run's log too,
    so they show in the Prefect UI beside the run."""
    run_logger = get_run_logger()
    sink = logger.add(
        lambda message: run_logger.log(message.record["level"].no, message.record["message"]),
        level="INFO",
        format="{message}",
    )
    try:
        yield
    finally:
        logger.remove(sink)


def collectable_commcells() -> list[str]:
    """Every CommCell block not marked collect=False, by name."""
    with get_client(sync_client=True) as client:
        documents = client.read_block_documents_by_type(BLOCK_TYPE_SLUG, include_secrets=False)
    return sorted(
        document.name
        for document in documents
        if document.name and document.data.get("collect", True) is not False
    )


def _blt_api(settings: CollectorSettings) -> APIClient:
    return APIClient(
        base_url=settings.blt_api_url,
        default_headers={"X-API-Key": settings.blt_api_key.get_secret_value()},
        timeout=settings.cv_timeout_seconds,
    )


def _with_commcell(
    commcell: str, kind: Kind, work: Callable[[CollectorSettings, BltStore, CommvaultClient], str]
) -> str:
    """Load the CommCell's blocks, take the same per-CommCell lock
    collect.sh takes, connect, and run `work`."""
    with logs_to_prefect():
        with run_lock(LOCKS, f"{commcell}.{kind}") as held:
            if not held:
                logger.warning("A {} for {} is already running, skipping.", kind, commcell)
                return "skipped: already running"
            settings, token_block = settings_from_blocks(commcell)
            commvault = build_client(settings, commcell, BlockTokenStore(token_block))
            with commvault, _blt_api(settings) as api:
                return work(settings, BltStore(api, commcell), commvault)


@flow(
    name="blt-collect-commcell",
    flow_run_name="collect-{commcell}",
    retries=1,
    retry_delay_seconds=300,
)
def collect_commcell(commcell: str) -> str:
    def work(settings: CollectorSettings, store: BltStore, cv: CommvaultClient) -> str:
        run = run_collection(
            CommvaultJobs(cv),
            store,
            initial_lookback_hours=settings.cv_initial_lookback_hours,
            history_limit_days=settings.cv_history_limit_days,
            overlap_minutes=settings.cv_overlap_minutes,
        )
        return f"run {run.id}: {run.status}, {run.jobs_collected} jobs"

    return _with_commcell(commcell, "collection", work)


@flow(
    name="blt-inventory-commcell",
    flow_run_name="inventory-{commcell}",
    retries=1,
    retry_delay_seconds=600,
)
def inventory_commcell(commcell: str) -> str:
    def work(settings: CollectorSettings, store: BltStore, cv: CommvaultClient) -> str:
        skipped: list[str] = []
        run = run_inventory(lambda: iter_inventory(cv, skipped), store, problems=skipped)
        if run.status != "succeeded":
            raise RuntimeError(f"Inventory run {run.id} {run.status}: {run.error}")
        return f"run {run.id}: {run.status}"

    return _with_commcell(commcell, "inventory", work)


def _every(commcells: list[str] | None, child: Callable[..., State], what: str) -> dict[str, str]:
    names = commcells or collectable_commcells()
    if not names:
        get_run_logger().warning("No CommCell blocks to %s - see docs/prefect.md", what)
        return {}
    outcome: dict[str, str] = {}
    failed: list[str] = []
    for name in names:
        state = child(name, return_state=True)
        if state.is_completed():
            outcome[name] = str(state.result())
        else:
            outcome[name] = f"{state.name}: {state.message}"
            failed.append(name)
    if failed:
        raise RuntimeError(f"Could not {what} for {', '.join(failed)}: {outcome}")
    return outcome


@flow(name="blt-collect-jobs")
def collect_jobs(commcells: list[str] | None = None) -> dict[str, str]:
    """Collect jobs from every CommCell block (or the ones named)."""
    return _every(commcells, collect_commcell, "collect jobs")


@flow(name="blt-collect-inventory")
def collect_inventory(commcells: list[str] | None = None) -> dict[str, str]:
    """Collect inventory from every CommCell block (or the ones named)."""
    return _every(commcells, inventory_commcell, "collect inventory")


@flow(name="blt-backfill", flow_run_name="backfill-{commcell}")
def backfill(commcell: str, until_complete: bool = True, max_batches: int = 200) -> str:
    """Fetch one CommCell's older history. Each batch is what one
    `collect.sh --backfill` does; with until_complete, batches repeat
    until the history is all in (or max_batches is reached)."""

    def work(settings: CollectorSettings, store: BltStore, cv: CommvaultClient) -> str:
        for batch in range(1, max_batches + 1):
            run = run_backfill(
                CommvaultJobs(cv),
                store,
                chunk_hours=settings.cv_backfill_chunk_hours,
                max_chunks=settings.cv_backfill_max_chunks,
                history_limit_days=settings.cv_history_limit_days,
                pause_seconds=settings.cv_backfill_pause_seconds,
            )
            if run is None:
                return f"complete after {batch - 1} batch(es)"
            if not until_complete:
                return f"one batch done (run {run.id}); more to do"
        return f"stopped after {max_batches} batches; more to do"

    return _with_commcell(commcell, "backfill", work)
