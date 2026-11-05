"""Storage monitoring: what the collectors post, and what the Storage
page reads back (blt.api.storage)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

GIB = 1024**3
BASE = "/v1/sources/commvault/lab/storage"


def _target(key: str, *, free: int, capacity: int = 100 * GIB, **extra: Any) -> dict[str, Any]:
    return {
        "product_key": key,
        "name": f"pool-{key}",
        "kind": "dedupe",
        "product_type": "DEDUPLICATION",
        "hosts": ["ma01"],
        "capacity_bytes": capacity,
        "free_bytes": free,
        **extra,
    }


def _post(http: TestClient, at: datetime, *targets: dict[str, Any], **batch: Any) -> Any:
    response = http.post(
        BASE, json={"collected_at": at.isoformat(), "targets": list(targets), **batch}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_collection_adds_targets_and_a_sample_each(http: TestClient) -> None:
    at = datetime.now(UTC) - timedelta(minutes=5)
    result = _post(http, at, _target("1", free=60 * GIB), _target("2", free=10 * GIB))
    assert result == {"received": 2, "new": 2, "samples": 2, "gone": 0}

    storage = http.get("/v1/storage").json()
    assert [s["name"] for s in storage] == ["pool-1", "pool-2"]
    first = storage[0]
    assert first["source_type"] == "commvault" and first["source_name"] == "lab"
    assert first["hosts"] == ["ma01"]
    # used is capacity less free when the product gives only those two
    assert first["latest"]["used_bytes"] == 40 * GIB
    assert first["trend"] is None  # one sample is no trend

    # the source is made the first time, like a run would
    sources = http.get("/v1/sources").json()
    assert [(s["type"], s["name"]) for s in sources] == [("commvault", "lab")]


def test_the_same_collection_twice_stores_one_sample(http: TestClient) -> None:
    at = datetime.now(UTC) - timedelta(minutes=5)
    _post(http, at, _target("1", free=60 * GIB))
    again = _post(http, at, _target("1", free=60 * GIB))
    assert again == {"received": 1, "new": 0, "samples": 0, "gone": 0}


def test_a_complete_collection_marks_what_it_no_longer_lists(http: TestClient) -> None:
    now = datetime.now(UTC)
    _post(http, now - timedelta(hours=2), _target("1", free=60 * GIB), _target("2", free=GIB))
    partial = _post(http, now - timedelta(hours=1), _target("1", free=59 * GIB), complete=False)
    assert partial["gone"] == 0
    result = _post(http, now, _target("1", free=58 * GIB))
    assert result["gone"] == 1

    assert [s["name"] for s in http.get("/v1/storage").json()] == ["pool-1"]
    gone = http.get("/v1/storage", params={"present": "false"}).json()
    assert [s["name"] for s in gone] == ["pool-2"]
    # still there by id, and its samples stay
    assert http.get(f"/v1/storage/{gone[0]['id']}").json()["present"] is False
    samples = http.get(f"/v1/storage/{gone[0]['id']}/samples").json()
    assert len(samples) == 1

    # and it is back when it is listed again
    _post(http, now + timedelta(minutes=1), _target("1", free=58 * GIB), _target("2", free=GIB))
    assert len(http.get("/v1/storage").json()) == 2


def test_an_older_collection_does_not_roll_a_target_back(http: TestClient) -> None:
    now = datetime.now(UTC)
    _post(http, now, _target("1", free=60 * GIB, name="renamed"))
    _post(http, now - timedelta(hours=1), _target("1", free=61 * GIB, name="old name"))
    (target,) = http.get("/v1/storage").json()
    assert target["name"] == "renamed"
    assert target["latest"]["free_bytes"] == 60 * GIB
    assert len(http.get(f"/v1/storage/{target['id']}/samples").json()) == 2


def test_the_trend_says_how_fast_it_fills_and_when_it_is_full(http: TestClient) -> None:
    now = datetime.now(UTC)
    # 2 GiB more used every day for 4 days, sampled every 6 hours
    for step in range(17):
        at = now - timedelta(hours=96 - 6 * step)
        used = 40 * GIB + int(step * 6 / 24 * 2 * GIB)
        _post(http, at, _target("1", free=100 * GIB - used))
    (target,) = http.get("/v1/storage").json()
    trend = target["trend"]
    assert trend["days"] == pytest.approx(4.0)
    assert trend["used_bytes_per_day"] == pytest.approx(2 * GIB, rel=0.01)
    # 52 GiB free at 2 GiB a day
    assert trend["days_to_full"] == pytest.approx(26, abs=0.2)
    assert datetime.fromisoformat(trend["full_at"]) > now + timedelta(days=25)


def test_a_shrinking_target_has_no_full_date(http: TestClient) -> None:
    now = datetime.now(UTC)
    _post(http, now - timedelta(hours=12), _target("1", free=50 * GIB))
    _post(http, now, _target("1", free=60 * GIB))
    (target,) = http.get("/v1/storage").json()
    assert target["trend"]["used_bytes_per_day"] < 0
    assert target["trend"]["days_to_full"] is None


def test_samples_by_day_keep_the_last_of_each_day(http: TestClient) -> None:
    day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    for hours, free in ((-30, 70), (-26, 69), (1, 68), (2, 67)):
        _post(http, day + timedelta(hours=hours), _target("1", free=free * GIB))
    (target,) = http.get("/v1/storage", params={"source_type": "commvault"}).json()
    every = http.get(f"/v1/storage/{target['id']}/samples").json()
    assert [s["free_bytes"] // GIB for s in every] == [70, 69, 68, 67]
    daily = http.get(f"/v1/storage/{target['id']}/samples", params={"every": "day"}).json()
    assert [s["free_bytes"] // GIB for s in daily] == [69, 67]


def test_filters(http: TestClient) -> None:
    at = datetime.now(UTC)
    _post(http, at, _target("1", free=GIB), {**_target("9", free=GIB), "kind": "cloud"})
    assert [s["kind"] for s in http.get("/v1/storage", params={"kind": "cloud"}).json()] == [
        "cloud"
    ]
    assert http.get("/v1/storage", params={"source_name": "other"}).json() == []


def test_an_unknown_target_is_404(http: TestClient) -> None:
    assert http.get(f"/v1/storage/{uuid.uuid4()}").status_code == 404
    assert http.get(f"/v1/storage/{uuid.uuid4()}/samples").status_code == 404


def test_storage_needs_the_api_key(http: TestClient) -> None:
    assert http.get("/v1/storage", headers={"X-API-Key": "wrong"}).status_code == 401
