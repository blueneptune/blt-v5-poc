"""One collection run for one CommCell.

Two passes, both ending in the same upsert:

1. Collect. Ask the API for the watermark (when the last successful run
   started). None -> an initial run: everything active now plus a short
   recent window (24 hours by default), so the current picture is right
   within minutes whatever the size of the CommCell. Otherwise a delta:
   everything active now, plus everything that finished since the
   watermark (less an overlap).

2. Reconcile. Any job the database still has as active that pass 1 did
   not return has changed state without showing up in the window - look
   each one up by id and store what it is now, or mark it missing if the
   CommCell no longer knows it.

After both, the database matches Commvault as of this run. The run is
only recorded as succeeded - and so only moves the watermark - if both
passes got all the way through.

History older than the initial window is a separate job, run_backfill()
below: it walks backwards a time slice at a time, in bounded batches, so
a long history never has to be pulled in one query.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Protocol

from loguru import logger

from blt.schemas import (
    MISSING_STATUS,
    BackfillUpdate,
    JobIn,
    JobPatch,
    ObjectIn,
    RunFinish,
    RunOut,
    RunStart,
)

from .store import BltStore


class JobSource(Protocol):
    """What collection needs from Commvault (blt.commvault.CommvaultClient
    in production, a fake in tests)."""

    def iter_job_pages(
        self,
        lookup_seconds: int,
        *,
        ended_between: tuple[datetime, datetime] | None = None,
    ) -> Iterator[list[JobIn]]: ...

    def get_job(self, job_id: int) -> JobIn | None: ...

    def oldest_job_start(self, lookup_seconds: int) -> datetime | None: ...


def _now() -> datetime:
    return datetime.now(UTC)


def run_collection(
    source: JobSource,
    store: BltStore,
    *,
    initial_lookback_hours: int = 24,
    history_limit_days: int = 3650,
    overlap_minutes: int = 60,
    force_full: bool = False,
    now: Callable[[], datetime] = _now,
) -> RunOut:
    # Taken before anything is fetched: this becomes the next run's
    # watermark, so it has to be no later than the data it vouches for.
    started_at = now()
    watermark = store.last_run().watermark

    if force_full:
        mode = "full"
        lookup_seconds = history_limit_days * 86_400
    elif watermark is None:
        mode = "initial"
        lookup_seconds = initial_lookback_hours * 3600
    else:
        mode = "delta"
        lookup_seconds = int((started_at - watermark).total_seconds()) + overlap_minutes * 60

    run = store.start_run(
        RunStart(mode=mode, started_at=started_at, lookup_seconds=lookup_seconds)  # type: ignore[arg-type]
    )
    logger.info(
        "Run {} started: mode={} watermark={} lookup={}s", run.id, mode, watermark, lookup_seconds
    )

    collected = reconciled = missing = 0
    try:
        for page in source.iter_job_pages(lookup_seconds):
            store.upsert_jobs(run.id, now(), page)
            collected += len(page)

        for stale in store.iter_active_unseen(run.id):
            current = source.get_job(stale.job_id)
            if current is None:
                logger.warning("Job {} is no longer in the CommCell, marking missing", stale.job_id)
                store.mark_job(
                    stale.job_id,
                    JobPatch(status=MISSING_STATUS, collected_at=now(), run_id=run.id),
                )
                missing += 1
            else:
                logger.info("Job {}: {} -> {}", stale.job_id, stale.status, current.status)
                store.upsert_jobs(run.id, now(), [current])
                reconciled += 1
    except BaseException as exc:
        store.finish_run(
            run.id,
            RunFinish(
                status="failed",
                finished_at=now(),
                jobs_collected=collected,
                jobs_reconciled=reconciled,
                jobs_missing=missing,
                error=f"{type(exc).__name__}: {exc}",
            ),
        )
        raise

    finished = store.finish_run(
        run.id,
        RunFinish(
            status="succeeded",
            finished_at=now(),
            jobs_collected=collected,
            jobs_reconciled=reconciled,
            jobs_missing=missing,
        ),
    )
    logger.info(
        "Run {} succeeded: {} collected, {} reconciled, {} missing",
        run.id,
        collected,
        reconciled,
        missing,
    )
    return finished


# Where the first backfill slice ends, relative to the oldest point
# ordinary runs already reached. Ordinary runs ask Commvault for "the
# last N seconds" by the CommServe's clock; a backfill names absolute
# times. If the two machines' clocks disagree, starting exactly at that
# point would leave a gap as wide as the disagreement - so start a day
# later and re-read the overlap, which upserts make free.
_BACKFILL_HEADROOM = timedelta(days=1)


def run_backfill(
    source: JobSource,
    store: BltStore,
    *,
    chunk_hours: int = 24,
    max_chunks: int = 30,
    history_limit_days: int = 3650,
    pause_seconds: float = 1.0,
    now: Callable[[], datetime] = _now,
    sleep: Callable[[float], None] = time.sleep,
) -> RunOut | None:
    """Collect older history, newest slice first, up to `max_chunks`
    slices per call. Safe to call repeatedly: it picks up where the last
    call stopped and returns None once there is nothing left to fetch.

    Only finished jobs are fetched here. Anything still active is the
    ordinary run's business, and is in every one of its windows.
    """
    state = store.backfill_state()
    if state.complete:
        logger.info(
            "Backfill is already complete (history collected back to {})", state.backfilled_to
        )
        return None
    if state.resume_from is None:
        raise RuntimeError(
            "Nothing has been collected for this CommCell yet - run a normal "
            "collection first, then backfill behind it."
        )

    started_at = now()
    limit = started_at - timedelta(days=history_limit_days)
    oldest = source.oldest_job_start(history_limit_days * 86_400)
    # One slice of margin below the oldest job: it is that job's *start*,
    # and slices are cut by end time.
    chunk = timedelta(hours=chunk_hours)
    floor = max(limit, oldest - chunk) if oldest is not None else started_at

    cursor = state.resume_from
    if state.backfilled_to is None:
        cursor = min(cursor + _BACKFILL_HEADROOM, started_at)

    if cursor <= floor:
        logger.info("Nothing older than {} to backfill - marking it complete", cursor)
        store.save_backfill(BackfillUpdate(backfilled_to=cursor, complete=True))
        return None

    run = store.start_run(RunStart(mode="backfill", started_at=started_at, lookup_seconds=0))
    logger.info(
        "Backfill run {} started: from {} back towards {} in {}h slices (at most {})",
        run.id,
        cursor,
        floor,
        chunk_hours,
        max_chunks,
    )

    collected = chunks = 0
    complete = False
    try:
        while chunks < max_chunks:
            slice_start = max(cursor - chunk, floor)
            jobs_in_slice = 0
            for page in source.iter_job_pages(
                history_limit_days * 86_400, ended_between=(slice_start, cursor)
            ):
                store.upsert_jobs(run.id, now(), page)
                jobs_in_slice += len(page)
            collected += jobs_in_slice
            chunks += 1
            cursor = slice_start
            complete = cursor <= floor
            # Saved after every slice, so a failure repeats one slice at most.
            store.save_backfill(BackfillUpdate(backfilled_to=cursor, complete=complete))
            logger.info("Backfill slice back to {}: {} jobs", cursor, jobs_in_slice)
            if complete:
                break
            sleep(pause_seconds)
    except BaseException as exc:
        store.finish_run(
            run.id,
            RunFinish(
                status="failed",
                finished_at=now(),
                jobs_collected=collected,
                error=f"{type(exc).__name__}: {exc}",
            ),
        )
        raise

    finished = store.finish_run(
        run.id, RunFinish(status="succeeded", finished_at=now(), jobs_collected=collected)
    )
    logger.info(
        "Backfill run {} succeeded: {} jobs in {} slices, history now back to {} - {}",
        run.id,
        collected,
        chunks,
        cursor,
        "complete" if complete else "more to do, run --backfill again",
    )
    return finished


def run_inventory(
    batches: Callable[[], Iterator[list[ObjectIn]]],
    store: BltStore,
    *,
    now: Callable[[], datetime] = _now,
) -> RunOut:
    """Collect what the source says exists - clients, instances,
    databases - rather than what it did. `batches` yields objects parents
    first (blt.commvault.inventory.iter_inventory in production).

    Recorded as an `inventory` run, which never touches the job
    watermark. When it succeeds the API marks everything it did not see
    as no longer present; a failed run marks nothing, because it saw
    less for a different reason.
    """
    run = store.start_run(RunStart(mode="inventory", started_at=now(), lookup_seconds=0))
    logger.info("Inventory run {} started", run.id)
    collected = 0
    try:
        for batch in batches():
            if batch:
                store.upsert_objects(run.id, now(), batch)
                collected += len(batch)
                logger.info("Inventory: {} {}(s)", len(batch), batch[0].kind)
    except BaseException as exc:
        store.finish_run(
            run.id,
            RunFinish(
                status="failed",
                finished_at=now(),
                jobs_collected=collected,
                error=f"{type(exc).__name__}: {exc}",
            ),
        )
        raise
    finished = store.finish_run(
        run.id, RunFinish(status="succeeded", finished_at=now(), jobs_collected=collected)
    )
    logger.info("Inventory run {} succeeded: {} objects", run.id, collected)
    return finished
