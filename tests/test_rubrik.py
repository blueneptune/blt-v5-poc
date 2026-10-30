"""Rubrik: collector logic -> API -> Postgres (the `rubrik` schema), with
only RSC itself faked; plus the SDK adapter and the Prefect pieces that
run without a server."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from rubrik_sdk import ActivitySeries, InventoryObject, ServiceAccount
from sdk_primer import APIClient

from blt.rubrik.collect import run_rubrik_collection, run_rubrik_inventory
from blt.rubrik.schemas import RubrikJobIn, RubrikObjectIn
from blt.rubrik.source import to_job_in, to_object_in
from blt.rubrik.store import RubrikStore

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def job(key: str, status: str = "Success", updated: datetime = T0, **fields: object) -> RubrikJobIn:
    return RubrikJobIn(
        **{  # type: ignore[arg-type]
            "series_key": key,
            "status": status,
            "is_active": status == "Running",
            "object_name": f"vm-{key}",
            "activity_type": "Backup",
            "start_time": updated - timedelta(minutes=30),
            "last_updated": updated,
            **fields,
        }
    )


class FakeRsc:
    def __init__(self) -> None:
        self.changed: list[RubrikJobIn] = []
        self.asked: list[datetime] = []
        self.fail_after_pages: int | None = None

    def iter_pages(self, updated_after: datetime) -> Iterator[list[RubrikJobIn]]:
        self.asked.append(updated_after)
        for number, start in enumerate(range(0, len(self.changed), 2)):
            if self.fail_after_pages is not None and number >= self.fail_after_pages:
                raise ConnectionError("RSC went away")
            yield self.changed[start : start + 2]


def obj(key: str, **fields: object) -> RubrikObjectIn:
    return RubrikObjectIn(
        **{  # type: ignore[arg-type]
            "object_key": key,
            "rsc_id": key,
            "name": f"obj-{key}",
            "object_type": "AzureNativeVm",
            "protection_status": "Protected",
            "compliance_status": "IN_COMPLIANCE",
            "sla_domain_name": "Gold",
            **fields,
        }
    )


class FakeInventory:
    def __init__(self, objects: list[RubrikObjectIn], fail_after_pages: int | None = None) -> None:
        self.objects = objects
        self.fail_after_pages = fail_after_pages

    def iter_pages(self) -> Iterator[list[RubrikObjectIn]]:
        for number, start in enumerate(range(0, len(self.objects), 2)):
            if self.fail_after_pages is not None and number >= self.fail_after_pages:
                raise ConnectionError("RSC went away")
            yield self.objects[start : start + 2]


@pytest.fixture
def store(blt_api: APIClient) -> RubrikStore:
    return RubrikStore(blt_api, "acme")


def clock(start: datetime) -> Iterator[datetime]:
    moment = start
    while True:
        yield moment
        moment += timedelta(seconds=1)


def test_first_run_takes_a_recent_window_then_deltas_reach_back_to_the_watermark(
    store: RubrikStore, http: TestClient
) -> None:
    rsc = FakeRsc()
    rsc.changed = [job("a", "Running"), job("b"), job("c", "Failure")]
    ticks = clock(T0)

    first = run_rubrik_collection(rsc, store, now=lambda: next(ticks))

    assert first.mode == "initial" and first.status == "succeeded"
    assert first.jobs_collected == 3
    assert rsc.asked[0] == T0 - timedelta(hours=24)

    # An hour later "a" has finished; a delta asks from the last start
    # less the overlap.
    rsc.changed = [job("a", "Success", updated=T0 + timedelta(minutes=50))]
    later = clock(T0 + timedelta(hours=1))
    second = run_rubrik_collection(rsc, store, overlap_minutes=10, now=lambda: next(later))

    assert second.mode == "delta"
    assert rsc.asked[1] == T0 - timedelta(minutes=10)
    rows = {j["series_key"]: j for j in http.get("/v1/sources/rubrik/acme/jobs").json()}
    assert rows["a"]["status"] == "Success" and rows["a"]["is_active"] is False
    assert rows["a"]["first_seen_at"] < rows["a"]["last_collected_at"]
    assert rows["c"]["status"] == "Failure"
    assert [
        j["series_key"] for j in http.get("/v1/sources/rubrik/acme/jobs?active=true").json()
    ] == []


def test_a_full_run_reaches_the_history_limit(store: RubrikStore) -> None:
    rsc = FakeRsc()
    ticks = clock(T0)

    run = run_rubrik_collection(
        rsc, store, force_full=True, history_limit_days=30, now=lambda: next(ticks)
    )

    assert run.mode == "full"
    assert rsc.asked == [T0 - timedelta(days=30)]


def test_a_failed_run_keeps_what_it_got_but_does_not_move_the_watermark(
    store: RubrikStore, http: TestClient
) -> None:
    rsc = FakeRsc()
    rsc.changed = [job("a"), job("b"), job("c"), job("d")]
    rsc.fail_after_pages = 1
    ticks = clock(T0)

    with pytest.raises(ConnectionError):
        run_rubrik_collection(rsc, store, now=lambda: next(ticks))

    last = http.get("/v1/sources/rubrik/acme/last-run").json()
    assert last["watermark"] is None
    assert last["last_run"]["status"] == "failed"
    assert last["last_run"]["jobs_collected"] == 2
    assert "RSC went away" in last["last_run"]["error"]
    assert len(http.get("/v1/sources/rubrik/acme/jobs").json()) == 2


def test_an_older_batch_does_not_overwrite_a_newer_one(http: TestClient) -> None:
    def post(collected: datetime, status: str) -> dict:
        body = {
            "collected_at": collected.isoformat(),
            "jobs": [job("a", status).model_dump(mode="json")],
        }
        return http.post("/v1/sources/rubrik/acme/jobs", json=body).json()

    assert post(T0, "Success") == {"received": 1, "written": 1}
    assert post(T0 - timedelta(minutes=5), "Running") == {"received": 1, "written": 0}
    assert http.get("/v1/sources/rubrik/acme/jobs").json()[0]["status"] == "Success"


def test_jobs_can_be_filtered(http: TestClient) -> None:
    jobs = [
        job("a", "Running", updated=T0),
        job("b", "Failure", updated=T0 + timedelta(minutes=1)),
        job("c", "Success", updated=T0 + timedelta(minutes=2)),
    ]
    http.post(
        "/v1/sources/rubrik/acme/jobs",
        json={"collected_at": T0.isoformat(), "jobs": [j.model_dump(mode="json") for j in jobs]},
    )

    def keys(query: str) -> list[str]:
        return [j["series_key"] for j in http.get(f"/v1/sources/rubrik/acme/jobs{query}").json()]

    assert keys("") == ["c", "b", "a"]  # newest change first
    assert keys("?active=true") == ["a"]
    assert keys("?status=failure") == ["b"]
    assert keys("?object_name=VM-C") == ["c"]
    assert keys("?limit=1&offset=1") == ["b"]


def test_jobs_can_be_filtered_by_activity_type(http: TestClient) -> None:
    jobs = [
        job("a", updated=T0),
        job("b", updated=T0 + timedelta(minutes=1), activity_type="Index"),
        job("c", updated=T0 + timedelta(minutes=2), activity_type="Replication"),
        job("d", updated=T0 + timedelta(minutes=3), activity_type="RadarAnalysis"),
    ]
    http.post(
        "/v1/sources/rubrik/acme/jobs",
        json={"collected_at": T0.isoformat(), "jobs": [j.model_dump(mode="json") for j in jobs]},
    )

    def keys(query: str) -> list[str]:
        return [j["series_key"] for j in http.get(f"/v1/sources/rubrik/acme/jobs{query}").json()]

    assert keys("") == ["d", "c", "b", "a"]  # every type is kept
    assert keys("?activity_type=backup") == ["a"]
    assert keys("?activity_type=Backup&activity_type=REPLICATION") == ["c", "a"]


def test_the_job_details_are_stored(http: TestClient) -> None:
    details = {
        "failure_reason": "Snapshot failed",
        "cause_error_code": "RBK-1",
        "last_activity_message": "Failed to back up",
        "percent_complete": 14.0,
        "attempt_number": 2,
        "is_on_demand": True,
        "logical_size": 107374182400,  # more than an int column holds
    }
    body = {
        "collected_at": T0.isoformat(),
        "jobs": [job("a", "Failure", **details).model_dump(mode="json")],
    }
    http.post("/v1/sources/rubrik/acme/jobs", json=body)

    stored = http.get("/v1/sources/rubrik/acme/jobs").json()[0]
    assert {name: stored[name] for name in details} == details


def test_an_unknown_account_has_no_jobs_but_a_normal_last_run(http: TestClient) -> None:
    assert http.get("/v1/sources/rubrik/nobody/jobs").status_code == 404
    assert http.get("/v1/sources/rubrik/nobody/last-run").json() == {
        "account": "nobody",
        "watermark": None,
        "last_run": None,
    }


def test_the_rubrik_endpoints_need_the_key(database_url: str) -> None:
    from blt.api.app import create_app

    with TestClient(create_app()) as anonymous:
        assert anonymous.get("/v1/sources/rubrik/acme/last-run").status_code == 401


def test_an_inventory_stores_objects_and_marks_the_ones_gone(
    store: RubrikStore, http: TestClient
) -> None:
    ticks = clock(T0)
    first = run_rubrik_inventory(
        FakeInventory([obj("a"), obj("b"), obj("c")]), store, now=lambda: next(ticks)
    )
    assert first.mode == "inventory" and first.status == "succeeded"
    assert first.jobs_collected == 3

    later = clock(T0 + timedelta(hours=12))
    run_rubrik_inventory(
        FakeInventory([obj("a", protection_status="NoSla"), obj("c")]),
        store,
        now=lambda: next(later),
    )

    rows = {o["object_key"]: o for o in http.get("/v1/sources/rubrik/acme/objects").json()}
    assert {k: o["present"] for k, o in rows.items()} == {"a": True, "b": False, "c": True}
    assert rows["a"]["protection_status"] == "NoSla"
    assert rows["a"]["first_seen_at"] < rows["a"]["last_seen_at"]
    assert rows["b"]["last_seen_at"] < rows["a"]["last_seen_at"]


def test_a_failed_inventory_keeps_what_it_got_and_marks_nothing_gone(
    store: RubrikStore, http: TestClient
) -> None:
    ticks = clock(T0)
    run_rubrik_inventory(
        FakeInventory([obj("a"), obj("b"), obj("c")]), store, now=lambda: next(ticks)
    )

    with pytest.raises(ConnectionError):
        run_rubrik_inventory(
            FakeInventory([obj("c"), obj("d"), obj("a")], fail_after_pages=1),
            store,
            now=lambda: next(ticks),
        )

    rows = {
        o["object_key"]: o["present"] for o in http.get("/v1/sources/rubrik/acme/objects").json()
    }
    assert rows == {"a": True, "b": True, "c": True, "d": True}


def test_an_inventory_does_not_touch_the_job_watermark(
    store: RubrikStore, http: TestClient
) -> None:
    ticks = clock(T0)
    run_rubrik_collection(FakeRsc(), store, now=lambda: next(ticks))
    run_rubrik_inventory(FakeInventory([obj("a")]), store, now=lambda: next(ticks))

    last = http.get("/v1/sources/rubrik/acme/last-run").json()
    assert last["watermark"] == T0.isoformat().replace("+00:00", "Z")
    assert last["last_run"]["mode"] == "initial"


def test_objects_can_be_filtered(store: RubrikStore, http: TestClient) -> None:
    ticks = clock(T0)
    run_rubrik_inventory(
        FakeInventory(
            [
                obj("a"),
                obj(
                    "b",
                    object_type="O365Mailbox",
                    protection_status="NoSla",
                    sla_domain_name=None,
                    compliance_status="UNPROTECTED",
                ),
                obj("c", object_type="O365Onedrive", sla_domain_name="Bronze"),
            ]
        ),
        store,
        now=lambda: next(ticks),
    )

    def keys(query: str) -> list[str]:
        return [o["object_key"] for o in http.get(f"/v1/sources/rubrik/acme/objects{query}").json()]

    assert keys("") == ["a", "b", "c"]  # by type, then name
    assert keys("?object_type=o365mailbox&object_type=O365Onedrive") == ["b", "c"]
    assert keys("?protection_status=nosla") == ["b"]
    assert keys("?compliance_status=unprotected") == ["b"]
    assert keys("?sla_domain=bronze") == ["c"]
    assert keys("?name=OBJ-A") == ["a"]
    assert keys("?present=false") == []
    assert keys("?limit=1&offset=1") == ["b"]


def test_objects_need_a_known_run(http: TestClient) -> None:
    http.post(
        "/v1/sources/rubrik/acme/runs",
        json={"mode": "inventory", "started_at": T0.isoformat(), "lookup_seconds": 0},
    )
    body = {"run_id": 999, "collected_at": T0.isoformat(), "objects": []}

    assert http.post("/v1/sources/rubrik/acme/objects", json=body).status_code == 404
    assert http.get("/v1/sources/rubrik/nobody/objects").status_code == 404


# -- without Postgres -------------------------------------------------------


def test_an_activity_series_becomes_a_job() -> None:
    series = ActivitySeries.model_validate(
        {
            "id": "1",
            "fid": "f-1",
            "activitySeriesId": "s-1",
            "startTime": "2026-10-28T10:00:00Z",
            "lastUpdated": "2026-10-28T10:30:00Z",
            "lastActivityType": "Backup",
            "lastActivityStatus": "Running",
            "objectName": "sql01",
            "objectType": "Mssql",
            "slaDomainName": "Gold",
            "progress": "14%",
            "failureReason": "",
            "causeErrorMessage": "Snapshot failed",
            "lastActivityMessage": "Taking snapshot",
            "attemptNumber": 1,
            "isOnDemand": False,
            "dataTransferred": 52428800,
            "cluster": {"id": "c-1", "name": "rubrik-east"},
        }
    )

    out = to_job_in(series)

    assert out.series_key == "f-1"
    assert out.is_active is True
    assert out.activity_type == "Backup" and out.status == "Running"
    assert out.cluster_name == "rubrik-east"
    assert out.last_updated == datetime(2026, 10, 28, 10, 30, tzinfo=UTC)
    assert out.raw["objectName"] == "sql01"
    assert out.percent_complete == 14.0
    assert out.failure_reason is None and out.cause_error_message == "Snapshot failed"
    assert out.last_activity_message == "Taking snapshot"
    assert out.attempt_number == 1 and out.is_on_demand is False
    assert out.data_transferred == 52428800


def test_an_rsc_object_becomes_an_inventory_row() -> None:
    rsc_object = InventoryObject.model_validate(
        {
            "id": "local-1",
            "fid": "f-1",
            "name": "mailbox-1",
            "objectType": "O365Mailbox",
            "location": "",
            "objectState": "ACTIVE",
            "protectionStatus": "Protected",
            "complianceStatus": "OUT_OF_COMPLIANCE",
            "lastSnapshot": "2026-10-30T02:00:00.000Z",
            "missedSnapshots": 3,
            "logicalBytes": 1073741824,
            "slaDomain": {"id": "sla-1", "name": "Gold"},
            "cluster": {"id": "c-1", "name": "rubrik-east"},
        }
    )

    out = to_object_in(rsc_object)

    assert out.object_key == "f-1" and out.rsc_id == "local-1"
    assert out.location is None
    assert out.sla_domain_id == "sla-1" and out.sla_domain_name == "Gold"
    assert out.cluster_name == "rubrik-east"
    assert out.last_snapshot == datetime(2026, 10, 30, 2, 0, tzinfo=UTC)
    assert out.missed_snapshots == 3 and out.logical_bytes == 1073741824


def test_the_block_holds_the_service_account_and_hides_the_secret() -> None:
    from blt.flows.rubrik import RubrikAccountBlock

    account = ServiceAccount(
        client_id="client|1",
        client_secret="s3cr3t",
        access_token_uri="https://acme.my.rubrik.com/api/client_token",
        name="blt-reader",
    )

    block = RubrikAccountBlock.from_service_account(account)

    assert block.service_account().base_url == "https://acme.my.rubrik.com"
    assert block.service_account().client_secret.get_secret_value() == "s3cr3t"
    assert "s3cr3t" not in repr(block)


def test_no_account_blocks_means_nothing_to_do_not_the_commcells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from blt.flows import rubrik

    monkeypatch.setattr(rubrik, "collectable_accounts", lambda: [])

    def refuse(*args: object, **kwargs: object) -> dict[str, str]:
        raise AssertionError("fan_out must not run with no accounts")

    monkeypatch.setattr(rubrik, "fan_out", refuse)
    monkeypatch.setattr(rubrik, "get_run_logger", lambda: logging.getLogger("test"))

    assert rubrik.rubrik_collect_jobs.fn() == {}


def test_no_account_blocks_means_no_inventory_either(monkeypatch: pytest.MonkeyPatch) -> None:
    from blt.flows import rubrik

    monkeypatch.setattr(rubrik, "collectable_accounts", lambda: [])

    def refuse(*args: object, **kwargs: object) -> dict[str, str]:
        raise AssertionError("fan_out must not run with no accounts")

    monkeypatch.setattr(rubrik, "fan_out", refuse)
    monkeypatch.setattr(rubrik, "get_run_logger", lambda: logging.getLogger("test"))

    assert rubrik.rubrik_collect_inventory.fn() == {}
