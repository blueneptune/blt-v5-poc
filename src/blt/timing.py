"""When each source is collected (TODO 24).

Each product's parent flow fires on its own schedule (prefect.yaml): jobs
hourly for CommCells and Rubrik accounts, every 15 minutes for NetBackup
primaries. Before, every source with Collect on started at that moment,
so 30 sources meant 30 runs at :07. Now, at each firing, the parent asks
`decide` about each source, and starts its run - if it is due in the
coming interval - **at a time of its own** in that interval:

- **default** (nothing set): every firing, at a stable offset from the
  firing - a hash of the source's name, inside the first half of the
  interval (at most 30 minutes). Thirty primaries on "every 15 minutes"
  start over seven and a half minutes instead of all at :02.
- **every N minutes** (`every_minutes`, never finer than the product's
  interval): at fixed times - multiples of N from midnight UTC, plus the
  same offset - so "every 4 hours" is the same four-hourly times every
  day, whatever the firings in between.
- **daily at HH:MM** (`daily_at`) in the source's time zone.
- a **quiet window** (`quiet_from` to `quiet_until`, in its time zone):
  no run of any kind starts inside it - a domain's busy backup hours. It
  may span midnight (22:00 to 04:00).

The rules for *when* (every, daily) are for job collection; storage and
inventory keep their product's schedule, spread the same way, and keep
out of the quiet window too.

Nothing is stored between firings: whether a source is due is worked out
from the clock alone, so a firing that did not happen (Prefect down)
only loses that interval's runs, and the next interval's run catches up
on everything since the last one that worked.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

Kind = Literal["jobs", "storage", "inventory"]

# Block fields (on each product's source block) and the timing field each
# stands for.
BLOCK_FIELDS = {
    "timing_every_minutes": "every_minutes",
    "timing_daily_at": "daily_at",
    "timing_quiet_from": "quiet_from",
    "timing_quiet_until": "quiet_until",
    "timing_timezone": "timezone",
}

HH_MM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_SPREAD_SECONDS = 30 * 60
DAY_MINUTES = 24 * 60


def _hh_mm(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    if len(text) == 4 and text[1] == ":":
        text = "0" + text
    if not HH_MM.match(text):
        raise ValueError("A time of day as HH:MM, e.g. 22:30.")
    return text


def _at(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


class SourceTiming(BaseModel):
    """One source's collection timing. All empty: the product's own."""

    every_minutes: int | None = Field(
        default=None,
        ge=15,
        le=7 * DAY_MINUTES,
        description="Collect jobs every so many minutes, at fixed times (multiples of it "
        "from midnight UTC). Never more often than the product's own schedule.",
        examples=[240],
    )
    daily_at: str | None = Field(
        default=None,
        description="Or collect jobs once a day, at this time (HH:MM, in `timezone`).",
        examples=["06:15"],
    )
    quiet_from: str | None = Field(
        default=None,
        description="No collection of any kind starts from this time (HH:MM, in `timezone`)...",
        examples=["22:00"],
    )
    quiet_until: str | None = Field(
        default=None, description="...until this one. It may span midnight.", examples=["04:00"]
    )
    timezone: str = Field(
        default="UTC",
        description="The time zone of `daily_at` and the quiet window (IANA, e.g. "
        "America/Chicago).",
        examples=["Europe/London"],
    )

    @field_validator("daily_at", "quiet_from", "quiet_until")
    @classmethod
    def _time(cls, value: str | None) -> str | None:
        return _hh_mm(value)

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        text = (value or "UTC").strip() or "UTC"
        try:
            ZoneInfo(text)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"No time zone {text!r}: an IANA name, e.g. Europe/London.") from error
        return text

    @model_validator(mode="after")
    def _consistent(self) -> SourceTiming:
        if self.every_minutes is not None and self.daily_at is not None:
            raise ValueError("Every so many minutes, or daily at a time: not both.")
        if (self.quiet_from is None) != (self.quiet_until is None):
            raise ValueError("A quiet window needs both its start and its end.")
        if self.quiet_from is not None and self.quiet_from == self.quiet_until:
            raise ValueError("A quiet window's start and end are the same time.")
        return self

    @property
    def is_default(self) -> bool:
        return self.every_minutes is None and self.daily_at is None and self.quiet_from is None

    def to_block(self) -> dict[str, Any]:
        """As the source block's fields."""
        return {block: getattr(self, field) for block, field in BLOCK_FIELDS.items()}

    @classmethod
    def from_block(cls, data: dict[str, Any]) -> SourceTiming:
        """From a source block's data. A block whose timing does not hold
        together - edited by hand - is collected as the product's own,
        rather than not at all."""
        values = {field: data.get(block) for block, field in BLOCK_FIELDS.items()}
        values = {k: v for k, v in values.items() if v is not None}
        try:
            return cls(**values)
        except ValueError:
            return cls()

    def describe(self) -> str:
        """A few words for the Sources list."""
        if self.daily_at:
            when = f"daily at {self.daily_at}"
        elif self.every_minutes:
            hours, minutes = divmod(self.every_minutes, 60)
            when = f"every {hours} h" if not minutes else f"every {self.every_minutes} min"
        else:
            when = "product's schedule"
        if self.quiet_from:
            when += f", quiet {self.quiet_from}-{self.quiet_until}"
        if (self.daily_at or self.quiet_from) and self.timezone != "UTC":
            when += f" {self.timezone}"
        return when


@dataclass(frozen=True)
class Decision:
    """Whether a source's run starts in this interval, and when."""

    due: bool
    start: datetime | None = None
    why: str | None = None

    def delay_seconds(self, now: datetime) -> float:
        """Seconds from `now` (the firing) to its start."""
        return max(0.0, (self.start - now).total_seconds()) if self.start else 0.0


def spread_seconds(interval_minutes: float) -> int:
    """How far after a firing the default runs are spread: the first
    half of the interval, at most 30 minutes."""
    return int(min(interval_minutes * 60 / 2, MAX_SPREAD_SECONDS))


def offset_seconds(name: str, interval_minutes: float) -> int:
    """The source's own offset, stable for its name."""
    spread = spread_seconds(interval_minutes)
    if spread <= 0:
        return 0
    digest = hashlib.sha256(name.encode()).digest()
    return int.from_bytes(digest[:8], "big") % spread


def in_quiet(timing: SourceTiming, when: datetime) -> bool:
    if timing.quiet_from is None or timing.quiet_until is None:
        return False
    local = when.astimezone(ZoneInfo(timing.timezone)).time()
    start, end = _at(timing.quiet_from), _at(timing.quiet_until)
    if start < end:
        return start <= local < end
    return local >= start or local < end  # across midnight


def _daily(timing: SourceTiming, now: datetime, until: datetime) -> datetime | None:
    """The daily time inside [now, until), if there is one."""
    assert timing.daily_at is not None
    zone = ZoneInfo(timing.timezone)
    at = _at(timing.daily_at)
    local_now = now.astimezone(zone)
    for days in (0, 1):
        day = (local_now + timedelta(days=days)).date()
        candidate = datetime.combine(day, at, zone).astimezone(UTC)
        if now <= candidate < until:
            return candidate
    return None


def _every(minutes: int, offset: int, now: datetime, until: datetime) -> datetime | None:
    """The first of the fixed times (multiples of `minutes` from midnight
    UTC, plus `offset` seconds) inside [now, until)."""
    step = minutes * 60
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since = (now - midnight).total_seconds() - offset
    k = -(-since // step)  # ceiling
    candidate = midnight + timedelta(seconds=k * step + offset)
    return candidate if candidate < until else None


def decide(
    name: str,
    timing: SourceTiming,
    now: datetime,
    interval_minutes: float,
    kind: Kind = "jobs",
) -> Decision:
    """Whether `name`'s run of `kind` starts in the interval beginning
    `now` (a firing of its product's parent flow), and when."""
    now = now.astimezone(UTC)
    until = now + timedelta(minutes=interval_minutes)
    offset = offset_seconds(name, interval_minutes)
    start: datetime | None
    if kind == "jobs" and timing.daily_at:
        start = _daily(timing, now, until)
        why = f"daily at {timing.daily_at}"
    elif kind == "jobs" and timing.every_minutes and timing.every_minutes > interval_minutes:
        start = _every(timing.every_minutes, offset, now, until)
        why = f"every {timing.every_minutes} minutes"
    else:
        start = now + timedelta(seconds=offset)
        why = None
    if start is None:
        return Decision(False, None, f"not due ({why})")
    if in_quiet(timing, start):
        return Decision(
            False, None, f"quiet {timing.quiet_from}-{timing.quiet_until} {timing.timezone}"
        )
    return Decision(True, start, why)


def next_starts(
    name: str,
    timing: SourceTiming,
    firing: datetime,
    interval_minutes: float,
    *,
    count: int = 3,
    kind: Kind = "jobs",
    horizon_days: int = 8,
) -> list[datetime]:
    """The next `count` starts, from the parent's next firing on, firing
    every `interval_minutes`; fewer if not within `horizon_days`."""
    found: list[datetime] = []
    when = firing
    end = firing + timedelta(days=horizon_days)
    while when < end and len(found) < count:
        decision = decide(name, timing, when, interval_minutes, kind)
        if decision.due and decision.start is not None:
            found.append(decision.start)
        when += timedelta(minutes=interval_minutes)
    return found
