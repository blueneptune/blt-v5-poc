"""A collection run's proof of life: where it starts from, a line now
and then while pages arrive, and how long it took."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from loguru import logger

from blt.progress import Progress, since_line, span


@pytest.fixture
def lines() -> Iterator[list[str]]:
    said: list[str] = []
    sink = logger.add(lambda message: said.append(message.record["message"]), level="INFO")
    yield said
    logger.remove(sink)


@pytest.mark.parametrize(
    ("seconds", "said"),
    [
        (0, "0s"),
        (45, "45s"),
        (60, "1m"),
        (725, "12m 5s"),
        (3600, "1h"),
        (12_000, "3h 20m"),
        (86_400, "1d"),
        (187_200, "2d 4h"),
    ],
)
def test_a_span_reads_as_people_say_it(seconds: float, said: str) -> None:
    assert span(seconds) == said


def test_the_start_line_says_from_when_and_how_far_back() -> None:
    now = datetime(2026, 11, 12, 17, 0, tzinfo=UTC)
    line = since_line("delta", now - timedelta(days=2, hours=3), now)
    assert line == "delta: changes since 2026-11-10 14:00 UTC (the last 2d 3h)"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_long_run_says_it_is_still_going_but_not_on_every_page(lines: list[str]) -> None:
    clock = Clock()
    progress = Progress("NetBackup run 7", every=30, clock=clock)

    for _ in range(5):  # 5 quick pages: nothing to say yet
        clock.now += 1
        progress.page(100)
    assert lines == []

    clock.now = 31
    progress.page(100)
    clock.now = 40  # 9 s later: too soon to say it again
    progress.page(100)
    clock.now = 75
    progress.page(50)

    assert lines == [
        "NetBackup run 7: still collecting - 600 jobs so far, 6 pages, 31s",
        "NetBackup run 7: still collecting - 750 jobs so far, 8 pages, 1m 15s",
    ]
    assert progress.elapsed() == "1m 15s"
