"""A ceiling on how many requests are sent per hour.

Commvault Cloud (SaaS) limits each tenant to 1000 read calls and 100
write calls an hour, counted across everything that uses the tenant's
API, and answers 429 beyond that. A normal collection is a handful of
calls; an inventory of a few hundred clients is not. Rather than run
into the limit and fail partway, the SDK counts its own calls and, when
the next one would exceed the budget, waits until the oldest call is an
hour old.

The count is per process: it stops one long run from exceeding the
budget, not several runs started close together. Leave headroom, and
schedule with that in mind.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

import httpx
from loguru import logger

_WINDOW = 3600.0


class CallBudget:
    def __init__(
        self,
        per_hour: int,
        *,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.per_hour = per_hour
        self._now = now
        self._sleep = sleep
        self._calls: deque[float] = deque()

    def spend(self) -> None:
        """Record one call, first waiting if the hour's budget is used up."""
        self._forget_old()
        if len(self._calls) >= self.per_hour:
            wait = self._calls[0] + _WINDOW - self._now() + 1
            logger.warning(
                "API call budget of {} an hour is used up - waiting {:.0f} minutes before the "
                "next request",
                self.per_hour,
                wait / 60,
            )
            self._sleep(wait)
            self._forget_old()
        self._calls.append(self._now())

    def _forget_old(self) -> None:
        cutoff = self._now() - _WINDOW
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()

    @property
    def used(self) -> int:
        self._forget_old()
        return len(self._calls)


class BudgetTransport(httpx.BaseTransport):
    """Counts every request against a CallBudget before sending it."""

    def __init__(self, inner: httpx.BaseTransport, budget: CallBudget) -> None:
        self._inner = inner
        self._budget = budget

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self._budget.spend()
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()
