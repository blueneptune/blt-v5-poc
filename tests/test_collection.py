"""The whole loop - collector logic -> API -> Postgres - with only
Commvault itself faked."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sdk_primer import APIClient

from blt.collector.collect import run_collection
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
        self.looked_up: list[int] = []

    def iter_job_pages(self, lookup_seconds: int) -> Iterator[list[JobIn]]:
        self.lookups.append(lookup_seconds)
        for start in range(0, len(self.listed), 2):
            yield self.listed[start : start + 2]

    def get_job(self, job_id: int) -> JobIn | None:
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

    # --- Run 1: nothing stored yet, so everything, as far back as it goes.
    commvault.listed = [
        _job(1, "Completed"),
        _job(2, "Running", percent_complete=40),
        _job(3, "Queued"),
        _job(4, "Waiting"),
    ]
    first = run_collection(commvault, store, initial_lookback_days=30, now=clock)

    assert first.mode == "full"
    assert first.status == "succeeded"
    assert first.jobs_collected == 4
    assert commvault.lookups == [30 * 86_400]
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
        def iter_job_pages(self, lookup_seconds: int) -> Iterator[list[JobIn]]:
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
