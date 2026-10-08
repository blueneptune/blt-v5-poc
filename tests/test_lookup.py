from datetime import UTC, datetime, timedelta

from blt.collector.lookup import compare, explain
from blt.schemas import JobIn, JobOut

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
START = NOW - timedelta(days=14)


def _stored(status: str = "Running", elapsed: int = 100, age_minutes: float = 10) -> JobOut:
    collected = NOW - timedelta(minutes=age_minutes)
    return JobOut(
        job_id=7,
        status=status,
        elapsed_seconds=elapsed,
        start_time=START,
        last_update_time=collected,
        commcell_id=1,
        state=status.lower(),
        is_active=status == "Running",
        first_seen_at=START,
        last_collected_at=collected,
        last_seen_run_id=3,
    )


def _live(status: str = "Running", elapsed: int = 100) -> JobIn:
    return JobIn(job_id=7, status=status, elapsed_seconds=elapsed, start_time=START)


def _differing(live: JobIn | None, stored: JobOut | None) -> list[str]:
    return [label for label, _, _, same in compare(live, stored) if not same]


def test_in_step() -> None:
    assert _differing(_live(), _stored()) == []
    assert "matches the CommServe" in explain(_live(), _stored(), NOW)


def test_a_field_commvault_omits_from_one_job_is_not_a_difference() -> None:
    # blt has a last update time (from the listing); a single-job lookup
    # never returns one.
    rows = {label: (a, same) for label, a, _, same in compare(_live(), _stored())}
    assert rows["last update time"] == ("(not in a single-job reply)", True)


def test_active_and_behind_says_how_far_and_what_to_check() -> None:
    live, stored = _live(elapsed=500), _stored(elapsed=100, age_minutes=600)
    assert _differing(live, stored) == ["elapsed seconds"]
    message = explain(live, stored, NOW)
    assert "still active" in message
    assert "10.0 hours ago" in message
    assert "collection_run" in message


def test_finished_since_blt_last_looked() -> None:
    message = explain(_live(status="Completed", elapsed=900), _stored(elapsed=100), NOW)
    assert "has finished" in message and "status" in message and "elapsed seconds" in message


def test_only_one_side_has_it() -> None:
    assert "blt does not" in explain(_live(), None, NOW)
    assert "CommServe no longer does" in explain(None, _stored(), NOW)
    assert "Neither" in explain(None, None, NOW)
