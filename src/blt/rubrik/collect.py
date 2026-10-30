"""One collection run for one RSC account.

Simpler than Commvault's (blt.collector.collect): RSC can be asked for
every job *changed* since a time, finished or not, so one pass does what
Commvault needs two for. A job still running changes as it goes and is
picked up again; a job that finishes changes and is picked up with its
result.

- initial: nothing collected yet - everything changed in a recent window
  (24 hours by default).
- delta:   everything changed since the watermark (when the last
  successful run started), less an overlap for clock differences
  between this host and RSC.
- full:    forced - everything changed within the history limit.

The run is recorded as succeeded, and so moves the watermark, only if
every page got through.

An inventory run (run_rubrik_inventory) is the other kind: every object
RSC knows about, protected or not. It never touches the watermark.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Protocol

from loguru import logger

from .schemas import (
    RubrikJobIn,
    RubrikObjectIn,
    RubrikRunFinish,
    RubrikRunMode,
    RubrikRunOut,
    RubrikRunStart,
)
from .store import RubrikStore


class RubrikJobSource(Protocol):
    def iter_pages(self, updated_after: datetime) -> Iterator[list[RubrikJobIn]]: ...


class RubrikObjectSource(Protocol):
    def iter_pages(self) -> Iterator[list[RubrikObjectIn]]: ...


def _now() -> datetime:
    return datetime.now(UTC)


def run_rubrik_collection(
    source: RubrikJobSource,
    store: RubrikStore,
    *,
    initial_lookback_hours: int = 24,
    history_limit_days: int = 365,
    overlap_minutes: int = 60,
    force_full: bool = False,
    now: Callable[[], datetime] = _now,
) -> RubrikRunOut:
    # Taken before anything is fetched: it becomes the next run's
    # watermark, so it must be no later than the data it vouches for.
    started_at = now()
    watermark = store.last_run().watermark

    mode: RubrikRunMode
    if force_full:
        mode, since = "full", started_at - timedelta(days=history_limit_days)
    elif watermark is None:
        mode, since = "initial", started_at - timedelta(hours=initial_lookback_hours)
    else:
        mode, since = "delta", watermark - timedelta(minutes=overlap_minutes)

    run = store.start_run(
        RubrikRunStart(
            mode=mode,
            started_at=started_at,
            lookup_seconds=int((started_at - since).total_seconds()),
        )
    )
    logger.info("Rubrik run {} started: mode={} changes since {}", run.id, mode, since)

    collected = 0
    try:
        for page in source.iter_pages(since):
            store.upsert_jobs(run.id, now(), page)
            collected += len(page)
    except BaseException as exc:
        store.finish_run(
            run.id,
            RubrikRunFinish(
                status="failed",
                finished_at=now(),
                jobs_collected=collected,
                error=f"{type(exc).__name__}: {exc}",
            ),
        )
        raise

    finished = store.finish_run(
        run.id, RubrikRunFinish(status="succeeded", finished_at=now(), jobs_collected=collected)
    )
    logger.info("Rubrik run {} succeeded: {} jobs", run.id, collected)
    return finished


def run_rubrik_inventory(
    source: RubrikObjectSource,
    store: RubrikStore,
    *,
    now: Callable[[], datetime] = _now,
) -> RubrikRunOut:
    """Every object RSC knows about, stored as an `inventory` run.

    When it succeeds the API marks every object it did not list as no
    longer present. A run that stops part way is recorded as failed and
    marks nothing: it saw less because it stopped, not because objects
    went away."""
    run = store.start_run(RubrikRunStart(mode="inventory", started_at=now(), lookup_seconds=0))
    logger.info("Rubrik inventory run {} started", run.id)
    collected = 0
    try:
        for page in source.iter_pages():
            store.upsert_objects(run.id, now(), page)
            collected += len(page)
    except BaseException as exc:
        store.finish_run(
            run.id,
            RubrikRunFinish(
                status="failed",
                finished_at=now(),
                jobs_collected=collected,
                error=f"{type(exc).__name__}: {exc}",
            ),
        )
        raise

    finished = store.finish_run(
        run.id, RubrikRunFinish(status="succeeded", finished_at=now(), jobs_collected=collected)
    )
    logger.info("Rubrik inventory run {} succeeded: {} objects", run.id, collected)
    return finished
