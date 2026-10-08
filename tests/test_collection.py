"""The whole loop - collector logic -> API -> Postgres - with only
Commvault itself faked."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sdk_primer import APIClient

from blt.collector.collect import run_backfill, run_collection
from blt.collector.store import BltStore
from blt.schemas import JobIn

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


class FakeCommvault:
    """`listed` is what the job query returns this run; `by_id` is what a
    per-job lookup can still find."""

    def __init__(self) -> None:
        self.listed: list[JobIn] = []
        self.by_id: dict[int, JobIn] = {}
        self.lookups: list[int] = []
        # What time-sliced (backfill) queries can find, and the slices asked for.
        self.history: list[JobIn] = []
        self.slices: list[tuple[datetime, datetime]] = []
        self.looked_up: list[int] = []

    def iter_pages(
        self,
        lookup_seconds: int,
        *,
        ended_between: tuple[datetime, datetime] | None = None,
    ) -> Iterator[list[JobIn]]:
        if ended_between is not None:
            # A slice of history: finished jobs by end time, inclusive.
            self.slices.append(ended_between)
            low, high = ended_between
            matching = [
                job
                for job in self.history
                if job.end_time is not None and low <= job.end_time <= high
            ]
            if matching:
                yield matching
            return
        self.lookups.append(lookup_seconds)
        for start in range(0, len(self.listed), 2):
            yield self.listed[start : start + 2]

    def oldest_start(self, lookup_seconds: int) -> datetime | None:
        starts = [job.start_time for job in self.history if job.start_time is not None]
        return min(starts) if starts else None

    def get(self, job_id: int) -> JobIn | None:
        self.looked_up.append(job_id)
        return self.by_id.get(job_id)


class Clock:
    def __init__(self, start: datetime) -> None:
        self.value = start

    def __call__(self) -> datetime:
        self.value += timedelta(seconds=1)
        return self.value


def _job(job_id: int, status: str, **fields: object) -> JobIn:
    return JobIn(job_id=job_id, status=status, client_name="web01", raw={"jobId": job_id}, **fields)  # type: ignore[arg-type]


def _states(http: TestClient, commcell: str = "prod") -> dict[int, str]:
    jobs = http.get(f"/commcells/{commcell}/jobs").json()
    return {job["job_id"]: job["state"] for job in jobs}


def test_full_then_delta_then_reconcile(http: TestClient, blt_api: APIClient) -> None:
    store = BltStore(blt_api, "prod")
    commvault = FakeCommvault()
    clock = Clock(T0)

    # Never collected: no watermark, and that is not an error.
    assert http.get("/commcells/prod/last-run").json()["watermark"] is None

    # --- Run 1: nothing stored yet, so what is active plus a short window.
    commvault.listed = [
        _job(1, "Completed"),
        _job(2, "Running", percent_complete=40),
        _job(3, "Queued"),
        _job(4, "Waiting"),
    ]
    first = run_collection(commvault, store, initial_lookback_hours=6, now=clock)

    assert first.mode == "initial"
    assert first.status == "succeeded"
    assert first.jobs_collected == 4
    assert commvault.lookups == [6 * 3600]
    assert commvault.looked_up == []  # everything active was just seen
    assert _states(http) == {1: "completed", 2: "running", 3: "queued", 4: "waiting"}
    first_seen = http.get("/commcells/prod/jobs/2").json()["first_seen_at"]

    # --- Run 2, an hour later. Job 2 finished and shows up in the delta
    # window. Job 3 finished but is NOT in the listing; job 4 is gone from
    # the CommCell altogether. Job 5 is new.
    clock.value = T0 + timedelta(hours=1)
    commvault.listed = [_job(2, "Completed", percent_complete=100), _job(5, "Running")]
    commvault.by_id = {3: _job(3, "Failed")}
    second = run_collection(commvault, store, overlap_minutes=10, now=clock)

    assert second.mode == "delta"
    # Reaches back to when run 1 started, plus the overlap.
    assert commvault.lookups[-1] == 3600 + 10 * 60
    # Only the two active jobs the listing missed were looked up by id.
    assert commvault.looked_up == [3, 4]
    assert (second.jobs_collected, second.jobs_reconciled, second.jobs_missing) == (2, 1, 1)
    assert _states(http) == {
        1: "completed",
        2: "completed",
        3: "failed",
        4: "missing",
        5: "running",
    }

    # One row per job, updated in place: same first_seen_at, new state.
    job2 = http.get("/commcells/prod/jobs/2").json()
    assert job2["first_seen_at"] == first_seen
    assert job2["percent_complete"] == 100
    assert job2["last_seen_run_id"] == second.id

    # "What is still in flight?" is now just job 5.
    active = http.get("/commcells/prod/jobs", params={"active": "true"}).json()
    assert [job["job_id"] for job in active] == [5]

    assert http.get("/commcells/prod/last-run").json()[
        "watermark"
    ] == second.started_at.isoformat().replace("+00:00", "Z")


def test_a_failed_run_does_not_move_the_watermark(http: TestClient, blt_api: APIClient) -> None:
    store = BltStore(blt_api, "prod")
    commvault = FakeCommvault()
    clock = Clock(T0)
    commvault.listed = [_job(1, "Running")]
    good = run_collection(commvault, store, now=clock)

    class Exploding(FakeCommvault):
        def iter_pages(
            self,
            lookup_seconds: int,
            *,
            ended_between: tuple[datetime, datetime] | None = None,
        ) -> Iterator[list[JobIn]]:
            yield [_job(2, "Running")]
            raise RuntimeError("CommServe went away")

    clock.value = T0 + timedelta(hours=1)
    try:
        run_collection(Exploding(), store, now=clock)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the run to fail")

    last = http.get("/commcells/prod/last-run").json()
    assert last["last_run"]["status"] == "failed"
    assert "CommServe went away" in last["last_run"]["error"]
    # Still run 1's start: the next run re-covers what the failed one didn't finish.
    assert datetime.fromisoformat(last["watermark"]) == good.started_at
    # What it did manage to post is kept.
    assert _states(http) == {1: "running", 2: "running"}


def test_commcells_are_kept_apart(http: TestClient, blt_api: APIClient) -> None:
    for name, status in (("prod", "Running"), ("dr", "Completed")):
        commvault = FakeCommvault()
        commvault.listed = [_job(100, status)]
        run_collection(commvault, BltStore(blt_api, name), now=Clock(T0))

    # Same job id, two CommCells, two independent rows.
    assert _states(http, "prod") == {100: "running"}
    assert _states(http, "dr") == {100: "completed"}


def test_an_older_batch_cannot_overwrite_a_newer_one(http: TestClient) -> None:
    def post(status: str, at: datetime) -> dict[str, int]:
        body = {
            "collected_at": at.isoformat(),
            "jobs": [_job(1, status).model_dump(mode="json")],
        }
        return http.post("/commcells/prod/jobs", json=body).json()

    assert post("Completed", T0 + timedelta(minutes=5))["written"] == 1
    assert post("Running", T0)["written"] == 0
    assert _states(http) == {1: "completed"}


def test_requests_without_the_key_are_rejected(http: TestClient) -> None:
    assert http.get("/commcells/prod/last-run", headers={"X-API-Key": "wrong"}).status_code == 401
    assert http.get("/healthz", headers={"X-API-Key": "wrong"}).status_code == 200


def test_forced_full_asks_for_the_whole_history(http: TestClient, blt_api: APIClient) -> None:
    commvault = FakeCommvault()
    run = run_collection(
        commvault, BltStore(blt_api, "prod"), history_limit_days=90, force_full=True, now=Clock(T0)
    )
    assert run.mode == "full"
    assert commvault.lookups == [90 * 86_400]


def _old_job(job_id: int, days_ago: float) -> JobIn:
    ended = T0 - timedelta(days=days_ago)
    return _job(job_id, "Completed", start_time=ended - timedelta(minutes=10), end_time=ended)


def test_backfill_walks_back_in_slices_and_resumes(http: TestClient, blt_api: APIClient) -> None:
    store = BltStore(blt_api, "prod")
    commvault = FakeCommvault()
    clock = Clock(T0)
    # History: one job a day, 1.5 to 9.5 days back.
    commvault.history = [_old_job(100 + n, n + 0.5) for n in range(1, 10)]

    # Nothing collected yet: there is nothing to backfill *behind*.
    with pytest.raises(RuntimeError, match="normal collection first"):
        run_backfill(commvault, store, now=clock, sleep=lambda _: None)

    # The initial run covers the last 24 hours only.
    commvault.listed = [_job(1, "Running")]
    initial = run_collection(commvault, store, initial_lookback_hours=24, now=clock)
    assert _states(http) == {1: "running"}

    # First batch: at most 4 one-day slices.
    first = run_backfill(
        commvault, store, chunk_hours=24, max_chunks=4, now=clock, sleep=lambda _: None
    )
    assert first is not None and first.mode == "backfill"
    assert len(commvault.slices) == 4
    # Starts a day *above* where the initial run reached, so a clock
    # disagreement with the CommServe can't leave a gap between them.
    top = commvault.slices[0][1]
    assert top > initial.started_at - timedelta(hours=24)
    # Slices are contiguous, newest first.
    for newer, older in zip(commvault.slices, commvault.slices[1:], strict=False):
        assert older[1] == newer[0]
    state = http.get("/commcells/prod/backfill").json()
    assert state["complete"] is False
    assert datetime.fromisoformat(state["backfilled_to"]) == commvault.slices[-1][0]
    stored = set(_states(http))
    assert {101, 102}.issubset(stored) and 109 not in stored

    # A backfill run must not move the watermark the deltas work from.
    assert datetime.fromisoformat(http.get("/commcells/prod/last-run").json()["watermark"]) == (
        initial.started_at
    )

    # Second batch picks up exactly where the first stopped, and finishes.
    resumed_from = commvault.slices[-1][0]
    run_backfill(commvault, store, chunk_hours=24, max_chunks=50, now=clock, sleep=lambda _: None)
    assert commvault.slices[4][1] == resumed_from
    assert http.get("/commcells/prod/backfill").json()["complete"] is True
    assert set(_states(http)) == {1, *range(101, 110)}
    # It stopped at the oldest job rather than walking back ten years.
    assert len(commvault.slices) < 20

    # Once complete, there is nothing left to do.
    before = len(commvault.slices)
    assert run_backfill(commvault, store, now=clock, sleep=lambda _: None) is None
    assert len(commvault.slices) == before


def test_backfill_after_a_forced_full_has_nothing_to_do(
    http: TestClient, blt_api: APIClient
) -> None:
    store = BltStore(blt_api, "prod")
    commvault = FakeCommvault()
    commvault.history = [_old_job(100, 40)]
    run_collection(commvault, store, force_full=True, now=Clock(T0))

    assert run_backfill(commvault, store, now=Clock(T0), sleep=lambda _: None) is None
    assert commvault.slices == []
    assert http.get("/commcells/prod/backfill").json()["complete"] is True
