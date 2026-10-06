"""One collection run for one CommCell.

Two passes, both ending in the same upsert:

1. Collect. Ask the API for the watermark (when the last successful run
   started). None -> full run, as far back as Commvault has history.
   Otherwise a delta: everything active now, plus everything that
   finished since the watermark (less an overlap).

2. Reconcile. Any job the database still has as active that pass 1 did
   not return has changed state without showing up in the window - look
   each one up by id and store what it is now, or mark it missing if the
   CommCell no longer knows it.

After both, the database matches Commvault as of this run. The run is
only recorded as succeeded - and so only moves the watermark - if both
passes got all the way through.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Protocol

from loguru import logger

from blt.schemas import MISSING_STATUS, JobIn, JobPatch, RunFinish, RunOut, RunStart

from .store import BltStore


class JobSource(Protocol):
    """What collection needs from Commvault (blt.commvault.CommvaultClient
    in production, a fake in tests)."""

    def iter_job_pages(self, lookup_seconds: int) -> Iterator[list[JobIn]]: ...

    def get_job(self, job_id: int) -> JobIn | None: ...


def _now() -> datetime:
    return datetime.now(UTC)


def run_collection(
    source: JobSource,
    store: BltStore,
    *,
    initial_lookback_days: int = 3650,
    overlap_minutes: int = 60,
    force_full: bool = False,
    now: Callable[[], datetime] = _now,
) -> RunOut:
    # Taken before anything is fetched: this becomes the next run's
    # watermark, so it has to be no later than the data it vouches for.
    started_at = now()
    watermark = store.last_run().watermark

    if watermark is None or force_full:
        mode = "full"
        lookup_seconds = initial_lookback_days * 86_400
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
