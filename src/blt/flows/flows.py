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
- collect_storage  - every CommCell: storage pools, capacity and use
                     (docs/storage.md). Hourly.

The "every CommCell" flows start one flow run per CommCell, all at once,
through the per-commcell deployments: each runs in its own process (a
pod, on a Kubernetes work pool), retries on its own, and one slow or
failing CommCell holds up no other. A global concurrency limit,
blt-commcell-runs, caps how many do work at the same time. The parent
waits for them, up to wait_minutes, and fails if any did.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

from commvault_sdk import CommvaultClient
from loguru import logger
from prefect import flow, get_run_logger
from prefect.client.orchestration import get_client
from prefect.client.schemas.objects import FlowRun, State
from prefect.concurrency.sync import concurrency
from prefect.deployments import run_deployment
from prefect.states import Cancelled
from sdk_primer import APIClient

from blt.collector.collect import run_backfill, run_collection, run_inventory
from blt.collector.connect import build_client
from blt.collector.inventory import iter_inventory
from blt.collector.settings import CollectorSettings
from blt.collector.source import CommvaultJobs
from blt.collector.store import BltStore
from blt.queues import SLOT_LIMIT, queue_for
from blt.storage.collect import collect_commvault_storage
from blt.storage.store import StorageStore
from blt.timing import Kind as TimingKind
from blt.timing import SourceTiming, decide

from .blocks import BLOCK_TYPE_SLUG, BlockTokenStore, settings_from_blocks
from .locks import source_lock

# How many CommCells may collect at once, across every flow: a global
# concurrency limit the worker creates on start (BLT_MAX_PARALLEL).
SLOTS = SLOT_LIMIT
COLLECT_ONE = "blt-collect-commcell/per-commcell"
INVENTORY_ONE = "blt-inventory-commcell/per-commcell"
STORAGE_ONE = "blt-storage-commcell/per-commcell"
_RUN_LABELS = {COLLECT_ONE: "collect", INVENTORY_ONE: "inventory", STORAGE_ONE: "storage"}
Kind = Literal["collection", "inventory", "backfill", "storage"]


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
    return _with_commcell_api(
        commcell, kind, lambda settings, api, cv: work(settings, BltStore(api, commcell), cv)
    )


def _with_commcell_api(
    commcell: str, kind: Kind, work: Callable[[CollectorSettings, APIClient, CommvaultClient], str]
) -> str:
    """_with_commcell, handing `work` the blt API client itself."""
    # A slot first (waiting for one if every slot is taken), then the
    # per-CommCell lock. Without the limit defined, nothing is capped.
    with logs_to_prefect(), concurrency(SLOTS, occupy=1, strict=False):
        with source_lock(f"commvault-{commcell}.{kind}") as held:
            if not held:
                logger.warning("A {} for {} is already running, skipping.", kind, commcell)
                return "skipped: already running"
            settings, token_block = settings_from_blocks(commcell)
            commvault = build_client(settings, commcell, BlockTokenStore(token_block))
            with commvault, _blt_api(settings) as api:
                return work(settings, api, commvault)


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


@flow(
    name="blt-storage-commcell",
    flow_run_name="storage-{commcell}",
    retries=1,
    retry_delay_seconds=120,
)
def storage_commcell(commcell: str) -> str:
    """One CommCell's storage pools, with capacity and use, posted as one
    sample each (docs/storage.md)."""

    def work(settings: CollectorSettings, api: APIClient, cv: CommvaultClient) -> str:
        return collect_commvault_storage(cv, StorageStore(api, "commvault", commcell))

    return _with_commcell_api(commcell, "storage", work)


# (deployment, source, seconds from now to start it) -> flow run id
StartRun = Callable[[str, str, float], str]


def _timings(block_type_slug: str) -> dict[str, SourceTiming]:
    with get_client(sync_client=True) as client:
        documents = client.read_block_documents_by_type(block_type_slug, include_secrets=False)
    return {d.name: SourceTiming.from_block(d.data) for d in documents if d.name}


def due_sources(
    names: list[str],
    block_type_slug: str,
    *,
    kind: TimingKind,
    interval_minutes: float,
    now: datetime | None = None,
    timings: Callable[[str], dict[str, SourceTiming]] | None = None,
) -> tuple[list[str], dict[str, float]]:
    """The sources whose run of `kind` starts in this firing's interval,
    and the seconds from now to each one's start (blt.timing). The rest
    are logged with why they are not due."""
    now = now or datetime.now(UTC)
    known = (timings or _timings)(block_type_slug)
    due: list[str] = []
    delays: dict[str, float] = {}
    log = get_run_logger()
    for name in names:
        decision = decide(name, known.get(name, SourceTiming()), now, interval_minutes, kind)
        if decision.due:
            due.append(name)
            delays[name] = decision.delay_seconds(now)
        else:
            log.info("%s: not this time - %s", name, decision.why)
    return due, delays


ReadState = Callable[[str], State]


def source_queue(block_type_slug: str, name: str) -> str:
    """The work queue for one source's runs: its collector's, or the
    default one blt's own worker takes (blt.queues)."""
    with get_client(sync_client=True) as client:
        document = client.read_block_document_by_name(
            name, block_type_slug=block_type_slug, include_secrets=False
        )
    return queue_for(document.data)


def _start_run(deployment: str, commcell: str, delay_seconds: float = 0) -> str:
    """Start one CommCell's flow run, on the worker that reaches it -
    `delay_seconds` from now - and return its id without waiting."""
    label = _RUN_LABELS[deployment]
    # run_deployment is typed as possibly a coroutine; called from sync
    # code it is not.
    run = cast(
        FlowRun,
        run_deployment(
            deployment,
            parameters={"commcell": commcell},
            flow_run_name=f"{label}-{commcell}",
            timeout=0,
            scheduled_time=starts_at(delay_seconds),
            work_queue_name=source_queue(BLOCK_TYPE_SLUG, commcell),
        ),
    )
    return str(run.id)


def starts_at(delay_seconds: float) -> datetime | None:
    """A flow run's scheduled start, `delay_seconds` from now; None (now)
    for none."""
    return datetime.now(UTC) + timedelta(seconds=delay_seconds) if delay_seconds > 0 else None


def _read_state(run_id: str) -> State:
    with get_client(sync_client=True) as client:
        state = client.read_flow_run(UUID(run_id)).state
    if state is None:
        raise RuntimeError(f"flow run {run_id} has no state")
    return state


def _cancel(run_id: str) -> None:
    with get_client(sync_client=True) as client:
        client.set_flow_run_state(
            UUID(run_id), Cancelled(message="No worker picked it up in time."), force=True
        )


def fan_out(
    commcells: list[str] | None,
    deployment: str,
    what: str,
    *,
    wait_minutes: float,
    delays: dict[str, float] | None = None,
    start: StartRun = _start_run,
    read_state: ReadState = _read_state,
    cancel: Callable[[str], None] = _cancel,
    poll_seconds: float = 10,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, str]:
    """Start a flow run per CommCell at once - each at its own time, with
    `delays` (seconds from now, from due_sources) - then wait for them all.

    Returns each CommCell's final state. Raises if any failed, or did
    not start; a run still going at the deadline is reported, not failed
    - it carries on, and its own result shows in the UI.

    The deadline is `wait_minutes` after each run's own start.
    A run no worker has picked up by its deadline is cancelled, and
    counts as failed: its collector is not running (a remote collector
    whose tunnel is down, docs/remote-collector.md). Left alone, the runs
    would pile up and all start at once when it is back.
    """
    names = commcells or collectable_commcells()
    log = get_run_logger()
    if not names:
        log.warning("No CommCell blocks to %s - see docs/prefect.md", what)
        return {}

    delays = delays or {}
    runs: dict[str, str] = {}
    outcome: dict[str, str] = {}
    for name in names:
        try:
            runs[name] = start(deployment, name, delays.get(name, 0.0))
        except Exception as exc:  # noqa: BLE001 - one bad start must not stop the rest
            outcome[name] = f"not started: {exc}"
    later = {n: delays[n] for n in runs if delays.get(n, 0) >= 60}
    log.info(
        "Started %d flow runs to %s: %s",
        len(runs),
        what,
        ", ".join(f"{n} (in {later[n] / 60:.0f} min)" if n in later else n for n in runs),
    )

    started = clock()
    deadlines = {n: started + delays.get(n, 0.0) + wait_minutes * 60 for n in runs}
    waiting = dict(runs)
    latest: dict[str, State] = {}
    while waiting:
        now = clock()
        for name, run_id in list(waiting.items()):
            state = latest[name] = read_state(run_id)
            if state.is_final():
                label = state.name or state.type.value
                outcome[name] = (
                    label
                    if state.is_completed()
                    else f"{label}: {state.message}"
                    if state.message
                    else label
                )
                del waiting[name]
        if not waiting or all(now >= deadlines[n] for n in waiting):
            break
        sleep(poll_seconds)
    for name, run_id in waiting.items():
        # AwaitingRetry is a SCHEDULED state too, but that run did start:
        # its collector is there.
        if latest[name].is_scheduled() and latest[name].name != "AwaitingRetry":
            try:
                cancel(run_id)
                done = "cancelled"
            except Exception as exc:  # noqa: BLE001 - the report matters more
                done = f"not cancelled: {exc}"
            outcome[name] = (
                f"not picked up in {wait_minutes:g} minutes - is its collector running? ({done})"
            )
        else:
            outcome[name] = f"still running after {wait_minutes:g} minutes"

    failed = [
        n
        for n, result in outcome.items()
        if result.startswith(("not started", "not picked up", "Failed", "Crashed", "Cancelled"))
    ]
    if failed:
        raise RuntimeError(f"Could not {what} for {', '.join(failed)}: {outcome}")
    return outcome


@flow(name="blt-collect-jobs")
def collect_jobs(
    commcells: list[str] | None = None, wait_minutes: float = 50, interval_minutes: float = 60
) -> dict[str, str]:
    """Collect jobs from every CommCell block that is due (or the ones
    named, at once), each in its own flow run at its own time
    (blt.timing). `interval_minutes`: how often this deployment fires."""
    return fan_out_due(
        commcells,
        COLLECT_ONE,
        "collect jobs",
        "jobs",
        wait_minutes=wait_minutes,
        interval_minutes=interval_minutes,
    )


@flow(name="blt-collect-inventory")
def collect_inventory(
    commcells: list[str] | None = None, wait_minutes: float = 300, interval_minutes: float = 720
) -> dict[str, str]:
    """Collect inventory from every CommCell block (or the ones named),
    each in its own flow run, spread over the start of the interval."""
    return fan_out_due(
        commcells,
        INVENTORY_ONE,
        "collect inventory",
        "inventory",
        wait_minutes=wait_minutes,
        interval_minutes=interval_minutes,
    )


@flow(name="blt-collect-storage")
def collect_storage(
    commcells: list[str] | None = None, wait_minutes: float = 30, interval_minutes: float = 60
) -> dict[str, str]:
    """Storage from every CommCell block (or the ones named), each in its
    own flow run, spread over the start of the interval."""
    return fan_out_due(
        commcells,
        STORAGE_ONE,
        "collect storage",
        "storage",
        wait_minutes=wait_minutes,
        interval_minutes=interval_minutes,
    )


def fan_out_due(
    given: list[str] | None,
    deployment: str,
    what: str,
    kind: TimingKind,
    *,
    wait_minutes: float,
    interval_minutes: float,
    collectable: Callable[[], list[str]] = collectable_commcells,
    block_type_slug: str = BLOCK_TYPE_SLUG,
    start: StartRun = _start_run,
    nothing: str = "No CommCell blocks to {what} - see docs/prefect.md",
    read_state: ReadState = _read_state,
) -> dict[str, str]:
    """A parent flow's firing: the sources named by hand all at once, or
    else every collectable source that is due (blt.timing), each at its
    own time."""
    if given:
        return fan_out(
            given, deployment, what, wait_minutes=wait_minutes, start=start, read_state=read_state
        )
    names = collectable()
    if not names:
        get_run_logger().warning(nothing.format(what=what))
        return {}
    due, delays = due_sources(names, block_type_slug, kind=kind, interval_minutes=interval_minutes)
    if not due:
        get_run_logger().info("Nothing is due to %s this time.", what)
        return {}
    return fan_out(
        due,
        deployment,
        what,
        wait_minutes=wait_minutes,
        delays=delays,
        start=start,
        read_state=read_state,
    )


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
