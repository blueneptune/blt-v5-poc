"""NetBackup jobs: collector logic -> API -> Postgres (the `netbackup`
schema, and common.job for the jobs page), with only NetBackup faked;
plus the SDK adapter, the common-job mapping, and the Prefect pieces
that run without a server."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from netbackup_sdk.models import job_from
from sdk_primer import APIClient

from blt.common.mapping import ACTIVE_STATES, KINDS, NETBACKUP_KINDS, netbackup_job, netbackup_state
from blt.netbackup.collect import run_netbackup_collection
from blt.netbackup.schemas import NetBackupJobIn
from blt.netbackup.source import to_job_in
from blt.netbackup.store import NetBackupStore

T0 = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
BASE = "/v1/sources/netbackup/nbu-work"

# Every jobType NetBackup 10.2's API lists (admin.yaml, GET /admin/jobs).
NETBACKUP_JOB_TYPES = [
    "BACKUP", "ARCHIVE", "RESTORE", "VERIFY", "DUPLICATE", "IMPORT", "DBBACKUP", "VAULT",
    "LABEL", "ERASE", "TPREQ", "TPCLEAN", "TPFORMAT", "VMPHYSINV", "DQTS", "DBRECOVER",
    "MCONTENTS", "IMAGEDELETE", "GENERIC", "REPLICATE", "REPLICA_IMPORT", "SNAPDUPE",
    "SNAPREPLICATE", "SNAPIMPORT", "APP_STATE_CAPTURE", "INDEXING", "INDEXCLEANUP", "SNAPSHOT",
    "SNAPINDEX", "ACTIVATE_IR", "DEACTIVATE_IR", "REACTIVATE_IR", "STOP_IR",
    "INSTANT_RECOVERY", "RMAN_CATALOG", "LOCKVM", "DEPLOYMENT", "INSTANT_ACCESS",
    "CLOUD_SNAPSHOT_REPLICATE", "PREPARING_FOR_BACKUP", "INSTANT_SYNC", "TRIAL_BACKUP",
    "MULTIPLE_STORAGE_METADATA",
]  # fmt: skip


def job(
    job_id: int,
    state: str = "DONE",
    status: int | None = 0,
    updated: datetime = T0,
    **fields: object,
) -> NetBackupJobIn:
    return NetBackupJobIn(
        **{  # type: ignore[arg-type]
            "job_id": job_id,
            "job_type": "BACKUP",
            "state": state,
            "status": status if state == "DONE" else None,
            "is_active": state != "DONE",
            "policy_name": "vm-gold",
            "policy_type": "VMWARE",
            "client_name": f"app{job_id:02d}.example.com",
            "storage_unit": "msdp-stu-01",
            "start_time": updated - timedelta(minutes=30),
            "end_time": updated if state == "DONE" else None,
            "last_update_time": updated,
            "kilobytes_transferred": 2048,
            **fields,
        }
    )


class FakeNetBackup:
    def __init__(self) -> None:
        self.changed: list[NetBackupJobIn] = []
        self.asked: list[datetime] = []
        self.fail_after_pages: int | None = None

    def iter_pages(self, updated_after: datetime) -> Iterator[list[NetBackupJobIn]]:
        self.asked.append(updated_after)
        for number, start in enumerate(range(0, len(self.changed), 2)):
            if self.fail_after_pages is not None and number >= self.fail_after_pages:
                raise ConnectionError("primary went away")
            yield self.changed[start : start + 2]


@pytest.fixture
def store(blt_api: APIClient) -> NetBackupStore:
    return NetBackupStore(blt_api, "nbu-work")


def clock(start: datetime) -> Iterator[datetime]:
    moment = start
    while True:
        yield moment
        moment += timedelta(seconds=1)


def test_first_run_takes_a_week_then_deltas_reach_back_to_the_watermark(
    store: NetBackupStore, http: TestClient
) -> None:
    nbu = FakeNetBackup()
    nbu.changed = [job(1, "ACTIVE"), job(2), job(3, status=58)]
    ticks = clock(T0)

    first = run_netbackup_collection(nbu, store, now=lambda: next(ticks))

    assert first.mode == "initial" and first.status == "succeeded"
    assert first.jobs_collected == 3
    assert nbu.asked[0] == T0 - timedelta(days=7)

    # Later job 1 is done; a delta asks from the last start less the overlap.
    nbu.changed = [job(1, "DONE", 0, updated=T0 + timedelta(minutes=10))]
    later = clock(T0 + timedelta(minutes=15))
    second = run_netbackup_collection(nbu, store, overlap_minutes=5, now=lambda: next(later))

    assert second.mode == "delta"
    assert nbu.asked[1] == T0 - timedelta(minutes=5)
    rows = {j["job_id"]: j for j in http.get(f"{BASE}/jobs").json()}
    assert rows[1]["state"] == "DONE" and rows[1]["is_active"] is False
    assert rows[1]["first_seen_at"] < rows[1]["last_collected_at"]
    assert rows[3]["status"] == 58
    assert http.get(f"{BASE}/jobs?active=true").json() == []


def test_a_full_run_reaches_the_history_limit(store: NetBackupStore) -> None:
    nbu = FakeNetBackup()
    ticks = clock(T0)

    run = run_netbackup_collection(
        nbu, store, force_full=True, history_limit_days=30, now=lambda: next(ticks)
    )

    assert run.mode == "full"
    assert nbu.asked == [T0 - timedelta(days=30)]


def test_a_failed_run_keeps_what_it_got_but_does_not_move_the_watermark(
    store: NetBackupStore, http: TestClient
) -> None:
    nbu = FakeNetBackup()
    nbu.changed = [job(1), job(2), job(3), job(4)]
    nbu.fail_after_pages = 1
    ticks = clock(T0)

    with pytest.raises(ConnectionError):
        run_netbackup_collection(nbu, store, now=lambda: next(ticks))

    last = http.get(f"{BASE}/last-run").json()
    assert last["watermark"] is None
    assert last["last_run"]["status"] == "failed"
    assert last["last_run"]["jobs_collected"] == 2
    assert "primary went away" in last["last_run"]["error"]
    assert len(http.get(f"{BASE}/jobs").json()) == 2


def test_an_older_batch_does_not_overwrite_a_newer_one(http: TestClient) -> None:
    def post(collected: datetime, state: str) -> dict:
        body = {
            "collected_at": collected.isoformat(),
            "jobs": [job(7, state).model_dump(mode="json")],
        }
        return http.post(f"{BASE}/jobs", json=body).json()

    assert post(T0, "DONE") == {"received": 1, "written": 1}
    assert post(T0 - timedelta(minutes=5), "ACTIVE") == {"received": 1, "written": 0}
    assert http.get(f"{BASE}/jobs").json()[0]["state"] == "DONE"


def test_jobs_can_be_filtered(store: NetBackupStore, http: TestClient) -> None:
    store.upsert_jobs(
        None,
        T0,
        [
            job(1),
            job(2, status=58),
            job(3, "ACTIVE"),
            job(4, job_type="DUPLICATE", backup_id="app04_1794150000"),
        ],
    )

    def ids(query: str) -> list[int]:
        return sorted(j["job_id"] for j in http.get(f"{BASE}/jobs?{query}").json())

    assert ids("active=true") == [3]
    assert ids("status=58") == [2]
    assert ids("job_type=duplicate") == [4]
    assert ids("client_name=APP01") == [1]
    assert ids("backup_id=app04_1794150000") == [4]
    assert http.get(f"{BASE}/jobs/2").json()["status"] == 58
    assert http.get(f"{BASE}/jobs/99").status_code == 404


def test_jobs_reach_the_jobs_page_in_the_common_shape(
    store: NetBackupStore, http: TestClient
) -> None:
    store.upsert_jobs(None, T0, [job(1), job(2, status=58), job(3, "ACTIVE")])

    jobs = {
        j["product_job_key"]: j
        for j in http.get("/v1/jobs?source_type=netbackup&kind=backup").json()
    }
    assert jobs["1"]["state"] == "succeeded" and jobs["1"]["source_name"] == "nbu-work"
    assert jobs["2"]["state"] == "failed"
    assert jobs["2"]["message"] == "status 58: can't connect to client"
    assert jobs["3"]["state"] == "running" and jobs["3"]["is_active"] is True
    assert jobs["1"]["bytes_transferred"] == 2048 * 1024
    assert jobs["1"]["protection"] == "vm-gold"
    assert jobs["1"]["product_path"] == f"{BASE}/jobs/1"

    detail = http.get(f"/v1/jobs/{jobs['2']['id']}").json()
    assert detail["product"]["status"] == 58
    assert detail["product"]["storage_unit"] == "msdp-stu-01"


def test_an_unknown_primary_has_no_jobs_but_a_normal_last_run(http: TestClient) -> None:
    assert http.get("/v1/sources/netbackup/nobody/last-run").json() == {
        "primary": "nobody",
        "watermark": None,
        "last_run": None,
    }
    assert http.get("/v1/sources/netbackup/nobody/jobs").status_code == 404


def test_the_netbackup_job_endpoints_need_the_key(database_url: str) -> None:
    from blt.api.app import create_app

    with TestClient(create_app()) as client:
        assert client.get(f"{BASE}/last-run").status_code == 401
        assert client.post(f"{BASE}/jobs", json={}).status_code == 401


# -- without Postgres -------------------------------------------------------


def test_a_netbackup_job_becomes_a_job_in() -> None:
    sdk_job = job_from(
        {
            "type": "job",
            "id": "48211",
            "attributes": {
                "jobId": 48211,
                "parentJobId": 48210,
                "jobType": "DUPLICATE",
                "state": "DONE",
                "status": 0,
                "policyName": "vm-gold",
                "policyType": "VMWARE",
                "clientName": "app01.example.com",
                "destinationMediaServerName": "media-b1",
                "destinationStorageUnitName": "msdp-b",
                "sourceMediaServerName": "media-a1",
                "sourceStorageUnitName": "msdp-a",
                "backupId": "app01.example.com_1794150000",
                "try": 2,
                "startTime": "2026-11-08T13:00:00.000Z",
                "endTime": "2026-11-08T13:21:00.000Z",
                "lastUpdateTime": "2026-11-08T13:21:00.000Z",
                "kilobytesTransferred": 1024,
                "kilobytesToTransfer": 4096,
                "numberOfFiles": 12,
                "percentComplete": 100,
            },
        }
    )

    out = to_job_in(sdk_job)

    assert out.job_id == 48211 and out.parent_job_id == 48210
    assert out.job_type == "DUPLICATE" and out.is_active is False
    assert (out.source_media_server, out.media_server) == ("media-a1", "media-b1")
    assert (out.source_storage_unit, out.storage_unit) == ("msdp-a", "msdp-b")
    assert out.backup_id == "app01.example.com_1794150000"
    assert out.try_number == 2 and out.kilobytes_to_transfer == 4096
    assert out.end_time == datetime(2026, 11, 8, 13, 21, tzinfo=UTC)
    assert out.raw["policyName"] == "vm-gold"


@pytest.mark.parametrize(
    ("state", "status", "expected"),
    [
        ("QUEUED", None, "queued"),
        ("ACTIVE", None, "running"),
        ("REQUEUED", None, "waiting"),
        ("SUSPENDED", None, "waiting"),
        ("INCOMPLETE", None, "waiting"),
        ("DONE", 0, "succeeded"),
        ("DONE", 1, "partial"),
        ("DONE", 150, "cancelled"),
        ("DONE", 196, "skipped"),
        ("DONE", 58, "failed"),
        ("DONE", None, "unknown"),
        ("SOMETHING_NEW", None, "unknown"),
    ],
)
def test_netbackup_states(state: str, status: int | None, expected: str) -> None:
    assert netbackup_state(state, status) == expected


def test_only_unfinished_jobs_count_as_active() -> None:
    assert netbackup_state("ACTIVE", None) in ACTIVE_STATES
    assert netbackup_state("INCOMPLETE", None) in ACTIVE_STATES
    for code in (0, 1, 58, 150, 196):
        assert netbackup_state("DONE", code) not in ACTIVE_STATES


def test_every_netbackup_job_type_maps_to_a_known_kind() -> None:
    assert set(NETBACKUP_KINDS.values()) <= set(KINDS)
    unmapped = [t for t in NETBACKUP_JOB_TYPES if t.lower() not in NETBACKUP_KINDS]
    # Left as `other` on purpose: not work on a client's data.
    assert unmapped == ["GENERIC", "INSTANT_SYNC", "MULTIPLE_STORAGE_METADATA"]


def test_the_common_shape_of_a_netbackup_job() -> None:
    row = {
        **job(5, "DONE", 13, kilobytes_data_transferred=1000, try_number=2).model_dump(),
        "first_seen_at": T0,
        "last_collected_at": T0,
    }
    out = netbackup_job(row)
    assert out["product_job_key"] == "5"
    assert out["kind"] == "backup" and out["state"] == "failed"
    assert out["product_status"] == "DONE (status 13)"
    assert out["message"] == "status 13: file read failed"
    # What was actually sent wins over NetBackup's estimate.
    assert out["bytes_transferred"] == 1000 * 1024
    assert out["asset_name"] == "app05.example.com" and out["asset_type"] == "VMWARE"
    assert out["attempt"] == 2 and out["ended_at"] == T0

    running = netbackup_job({**row, "state": "ACTIVE", "status": None})
    assert running["ended_at"] is None and running["message"] is None
    assert running["product_status"] == "ACTIVE"
    unknown_code = netbackup_job({**row, "status": 4207})
    assert unknown_code["message"] == "status 4207"
