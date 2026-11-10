"""When each source is collected (blt.timing): spread, every N, daily,
quiet windows - worked out from the clock alone."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from blt.timing import (
    SourceTiming,
    decide,
    next_starts,
    offset_seconds,
    spread_seconds,
)

FIRING = datetime(2026, 11, 10, 9, 7, tzinfo=UTC)  # an hourly firing at :07


def test_default_runs_spread_over_the_first_half_of_the_interval() -> None:
    names = [f"nbu-{i:02}" for i in range(30)]
    offsets = [offset_seconds(n, 15) for n in names]
    assert all(0 <= o < 450 for o in offsets)  # 7.5 minutes for every 15
    assert len(set(offsets)) > 20  # spread, not bunched
    assert offset_seconds("nbu-01", 15) == offset_seconds("nbu-01", 15)  # stable
    assert spread_seconds(60) == 1800 and spread_seconds(720) == 1800


def test_a_default_source_is_due_every_firing_at_its_own_offset() -> None:
    decision = decide("cv-toaster", SourceTiming(), FIRING, 60)
    assert decision.due
    assert decision.start == FIRING + timedelta(seconds=offset_seconds("cv-toaster", 60))
    assert 0 <= decision.delay_seconds(FIRING) < 1800


def test_every_four_hours_is_the_same_times_every_day() -> None:
    timing = SourceTiming(every_minutes=240)
    starts = next_starts("cv-big", timing, FIRING, 60, count=6)
    offset = offset_seconds("cv-big", 60)
    assert [s.hour for s in starts] == [12, 16, 20, 0, 4, 8]
    assert all(s.minute * 60 + s.second == offset for s in starts)
    # Firings in between are not due.
    assert not decide("cv-big", timing, FIRING, 60).due


def test_every_more_often_than_the_product_is_the_product_s_own() -> None:
    timing = SourceTiming(every_minutes=15)
    assert decide("cv-toaster", timing, FIRING, 60).due  # hourly, as the product


def test_daily_at_a_time_in_its_own_time_zone() -> None:
    timing = SourceTiming(daily_at="06:15", timezone="America/Chicago")  # 12:15 UTC in November
    starts = next_starts("nbu-omaha", timing, FIRING, 15, count=2)
    assert starts == [
        datetime(2026, 11, 10, 12, 15, tzinfo=UTC),
        datetime(2026, 11, 11, 12, 15, tzinfo=UTC),
    ]
    firing = datetime(2026, 11, 10, 12, 2, tzinfo=UTC)
    at_firing = decide("nbu-omaha", timing, firing, 15)
    assert at_firing.due and at_firing.delay_seconds(firing) == 780


def test_daily_across_a_change_of_clocks() -> None:
    timing = SourceTiming(daily_at="06:15", timezone="Europe/London")
    before, after = next_starts("uk", timing, datetime(2026, 10, 24, 0, 0, tzinfo=UTC), 60, count=2)
    assert before.hour == 5 and after.hour == 6  # BST, then GMT from 25 October


def test_nothing_starts_in_a_quiet_window_across_midnight() -> None:
    timing = SourceTiming(quiet_from="22:00", quiet_until="04:00")
    night = datetime(2026, 11, 10, 23, 7, tzinfo=UTC)
    decision = decide("cv-toaster", timing, night, 60)
    assert not decision.due and decision.why == "quiet 22:00-04:00 UTC"
    assert decide("cv-toaster", timing, datetime(2026, 11, 10, 4, 7, tzinfo=UTC), 60).due
    # Storage and inventory keep out of it too.
    assert not decide("cv-toaster", timing, night, 60, "storage").due


def test_storage_and_inventory_keep_the_product_s_schedule() -> None:
    timing = SourceTiming(daily_at="06:15")
    assert decide("cv-toaster", timing, FIRING, 60, "storage").due
    assert not decide("cv-toaster", timing, FIRING, 60, "jobs").due


@pytest.mark.parametrize(
    "fields",
    [
        {"every_minutes": 240, "daily_at": "06:00"},
        {"quiet_from": "22:00"},
        {"quiet_from": "22:00", "quiet_until": "22:00"},
        {"daily_at": "25:00"},
        {"timezone": "Mars/Olympus"},
        {"every_minutes": 5},
    ],
)
def test_timing_that_does_not_hold_together_is_refused(fields: dict) -> None:
    with pytest.raises(ValidationError):
        SourceTiming(**fields)


def test_a_block_s_timing_and_back() -> None:
    timing = SourceTiming(daily_at="6:15", quiet_from="22:00", quiet_until="04:00")
    assert timing.daily_at == "06:15"
    assert SourceTiming.from_block(timing.to_block()) == timing
    assert SourceTiming.from_block({}).is_default
    # Edited by hand into nonsense: collected as the product's own.
    assert SourceTiming.from_block({"timing_quiet_from": "22:00"}).is_default


def test_described_in_a_few_words() -> None:
    assert SourceTiming().describe() == "product's schedule"
    assert SourceTiming(every_minutes=240).describe() == "every 4 h"
    assert (
        SourceTiming(daily_at="06:15", timezone="America/Chicago").describe()
        == "daily at 06:15 America/Chicago"
    )
    assert (
        SourceTiming(quiet_from="22:00", quiet_until="04:00").describe()
        == "product's schedule, quiet 22:00-04:00"
    )
