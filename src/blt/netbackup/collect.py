"""One job collection run for one NetBackup primary.

The same shape as Rubrik's (blt.rubrik.collect): NetBackup can be asked
for every job changed since a time (lastUpdateTime), finished or not, so
one pass picks up new jobs, jobs that moved on, and jobs that finished.

- initial: nothing collected yet - every job changed in a recent window.
           NetBackup only keeps finished jobs a few days, so this is in
           practice "everything the primary still has".
- delta:   every job changed since the watermark (when the last
           successful run started), less an overlap for clock
           differences between this host and the primary.
- full:    forced - every job changed within the history limit.

The run is recorded as succeeded, and so moves the watermark, only if
every page got through.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Protocol

from loguru import logger

from .schemas import (
    NetBackupJobIn,
    NetBackupRunFinish,
    NetBackupRunMode,
    NetBackupRunOut,
    NetBackupRunStart,
)
from .store import NetBackupStore


class NetBackupJobSource(Protocol):
    def iter_pages(self, updated_after: datetime) -> Iterator[list[NetBackupJobIn]]: ...


def _now() -> datetime:
    return datetime.now(UTC)


def run_netbackup_collection(
    source: NetBackupJobSource,
    store: NetBackupStore,
    *,
    initial_lookback_hours: int = 24 * 7,
    history_limit_days: int = 30,
    overlap_minutes: int = 30,
    force_full: bool = False,
    now: Callable[[], datetime] = _now,
) -> NetBackupRunOut:
    # Taken before anything is fetched: it becomes the next run's
    # watermark, so it must be no later than the data it vouches for.
    started_at = now()
    watermark = store.last_run().watermark

    mode: NetBackupRunMode
    if force_full:
        mode, since = "full", started_at - timedelta(days=history_limit_days)
    elif watermark is None:
        mode, since = "initial", started_at - timedelta(hours=initial_lookback_hours)
    else:
        mode, since = "delta", watermark - timedelta(minutes=overlap_minutes)

    run = store.start_run(
        NetBackupRunStart(
            mode=mode,
            started_at=started_at,
            lookup_seconds=int((started_at - since).total_seconds()),
        )
    )
    logger.info("NetBackup run {} started: mode={} changes since {}", run.id, mode, since)

    collected = 0
    try:
        for page in source.iter_pages(since):
            store.upsert_jobs(run.id, now(), page)
            collected += len(page)
    except BaseException as exc:
        store.finish_run(
            run.id,
            NetBackupRunFinish(
                status="failed",
                finished_at=now(),
                jobs_collected=collected,
                error=f"{type(exc).__name__}: {exc}",
            ),
        )
        raise

    finished = store.finish_run(
        run.id,
        NetBackupRunFinish(status="succeeded", finished_at=now(), jobs_collected=collected),
    )
    logger.info("NetBackup run {} succeeded: {} jobs", run.id, collected)
    return finished
