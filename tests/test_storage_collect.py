"""The Commvault storage collector (blt.storage.commvault), against the
answers cv-toaster gave (yoki-py-commvault-sdk docs/live), and posted
through to the API."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from commvault_sdk import CommvaultClient
from sdk_primer import APIClient, ServerError

from blt.storage.collect import collect_commvault_storage
from blt.storage.commvault import MB, commvault_storage
from blt.storage.store import StorageStore

POOL = {
    "totalUsableFreeSpace": 1005050,
    "sizeOnDisk": 24144,
    "totalCapacity": 1048572,
    "totalFreeSpace": 1025530,
    "status": "Online",
    "libraryList": [{"_type_": 9, "libraryId": 1}],
    "storagePolicyEntity": {"storagePolicyName": "pool-01", "storagePolicyId": 2},
    "storagePoolEntity": {"storagePoolName": "pool-01", "storagePoolId": 2},
}
CLOUD = {
    "totalCapacity": -1,
    "totalFreeSpace": -1,
    "sizeOnDisk": 512,
    "status": "Online",
    "storagePoolEntity": {"storagePoolName": "s3-01", "storagePoolId": 7},
}


def _raise(error: Exception) -> Any:
    def call(*_: Any, **__: Any) -> Any:
        raise error

    return call


def _fake(**overrides: Any) -> CommvaultClient:
    raw = SimpleNamespace(
        storage=SimpleNamespace(list_storage_pool=lambda: {"storagePoolList": [POOL, CLOUD]}),
        disk_storage=SimpleNamespace(
            list_storage_disk=lambda: {
                "diskStorage": [{"id": 2, "storagePoolType": "DEDUPLICATION"}]
            },
            get_storage_disk=lambda pool_id: {
                "general": {"dedupeSavingsPercent": 86.91},
                "backupLocations": [{"id": 1, "name": "mount-01", "status": "Ready"}],
                "associatedPlans": [{"planName": "plan-01", "planId": 1}],
            },
        ),
        cloud_storage=SimpleNamespace(
            list_storage_cloud=lambda: {"cloudStorage": [{"id": 7, "storagePoolType": "CLOUD"}]}
        ),
        tape_storage=SimpleNamespace(list_storage_tape=lambda: {"tapeStorage": []}),
        local_storage=SimpleNamespace(
            list_storage_local=lambda: {
                "localStorage": [{"id": 2, "storagePoolType": "NON_DEDUPLICATION"}]
            }
        ),
        media_agent=SimpleNamespace(
            list_media_agent=lambda: {
                "mediaAgents": [{"id": 2, "name": "ma-02"}, {"id": 3, "name": "ma-01"}]
            },
            get_media_agent_associations=lambda agent_id: {
                "associations": [{"entityType": "Storage", "id": 2}]
                + ([{"entityType": "Storage", "id": 7}] if agent_id == 3 else [])
            },
        ),
    )
    for path, value in overrides.items():
        area, name = path.split(".")
        setattr(getattr(raw, area), name, value)
    return cast(CommvaultClient, SimpleNamespace(raw=raw))


def test_a_dedupe_pool_with_its_media_agents_and_numbers() -> None:
    pool, cloud = commvault_storage(_fake())
    assert (pool.product_key, pool.name, pool.kind) == ("2", "pool-01", "dedupe")
    # the disk list's word, not the local list's
    assert pool.product_type == "DEDUPLICATION"
    assert pool.hosts == ["ma-01", "ma-02"]
    assert pool.capacity_bytes == 1048572 * MB
    assert pool.free_bytes == 1025530 * MB
    assert pool.used_bytes == (1048572 - 1025530) * MB
    assert pool.physical_bytes == 24144 * MB
    # 86.91 % saved: 7.64 times as much data as it takes on disk
    assert pool.dedupe_ratio == 7.64
    assert pool.logical_bytes == int(24144 * MB * 7.64)
    assert pool.attributes["mount_paths"] == [{"name": "mount-01", "status": "Ready"}]
    assert pool.attributes["plans"] == ["plan-01"]
    assert pool.attributes["usable_free_bytes"] == 1005050 * MB


def test_a_cloud_pool_has_no_capacity_to_speak_of() -> None:
    _, cloud = commvault_storage(_fake())
    assert (cloud.kind, cloud.hosts) == ("cloud", ["ma-01"])
    # Commvault says -1 where it does not know
    assert cloud.capacity_bytes is None and cloud.free_bytes is None
    assert cloud.used_bytes is None
    assert cloud.physical_bytes == 512 * MB


def test_a_list_that_fails_is_a_problem_not_the_end() -> None:
    problems: list[str] = []
    fake = _fake(
        **{
            "cloud_storage.list_storage_cloud": _raise(ServerError("GET /V4/Storage/Cloud -> 500")),
            "media_agent.get_media_agent_associations": _raise(ServerError("-> 500")),
        }
    )
    pool, cloud = commvault_storage(fake, problems)
    assert cloud.kind == "other" and pool.hosts == []
    assert len(problems) == 3  # the cloud list and both MediaAgents


def test_collected_and_posted(blt_api: APIClient) -> None:
    summary = collect_commvault_storage(_fake(), StorageStore(blt_api, "commvault", "lab"))
    assert summary == "2 storage pools, 2 samples"
    listed = blt_api.get("/v1/storage").json()
    assert [(t["name"], t["kind"]) for t in listed] == [("pool-01", "dedupe"), ("s3-01", "cloud")]
    assert listed[0]["latest"]["dedupe_ratio"] == pytest.approx(7.64)
