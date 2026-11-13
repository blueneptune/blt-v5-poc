"""Proof of life for a collection run, in its log.

A run logs where it starts from, then a line as pages arrive (at most one
every `every` seconds, so a quick run logs none and a long catch-up shows
it is still moving), then how long it took. Every product's collection
loop uses it, so a run in the Prefect UI reads the same whichever product
it collects.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime

from loguru import logger


def span(seconds: float) -> str:
    """A length of time as people say it: 45s, 12m 5s, 3h 20m, 2d 4h."""
    whole = int(seconds)
    days, rest = divmod(whole, 86_400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    return f"{secs}s"


def since_line(mode: str, since: datetime, started_at: datetime) -> str:
    """What a run is about to collect: `delta: changes since 2026-11-10
    14:00 UTC (the last 2d 3h)`."""
    back = span((started_at - since).total_seconds())
    return f"{mode}: changes since {since:%Y-%m-%d %H:%M} UTC (the last {back})"


class Progress:
    """Counts what a run stores and says so now and then."""

    def __init__(
        self,
        label: str,
        *,
        every: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.label = label
        self.every = every
        self._clock = clock
        self._started = clock()
        self._said = self._started
        self.pages = 0
        self.items = 0

    def elapsed(self) -> str:
        return span(self._clock() - self._started)

    def page(self, count: int, noun: str = "jobs") -> None:
        self.pages += 1
        self.items += count
        if self._clock() - self._said >= self.every:
            self._said = self._clock()
            logger.info(
                "{}: still collecting - {} {} so far, {} pages, {}",
                self.label,
                self.items,
                noun,
                self.pages,
                self.elapsed(),
            )
