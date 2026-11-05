"""The NetBackup storage reader (blt.storage.netbackup), against the
answers eth00bck01 gave (yoki-py-netbackup-sdk docs/live)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from netbackup_sdk import NetBackupClient

from blt.storage.netbackup import PAGE, UNLIMITED, netbackup_storage


def _pool(name: str, raw: int, free: int, used: int) -> dict[str, Any]:
    return {
        "type": "diskPool",
        "id": f"PureDisk:{name}",
        "attributes": {
            "name": name,
            "sType": "PureDisk",
            "storageCategory": "MSDP",
            "diskVolumes": [{"name": "PureDiskVolume", "state": "UP", "rawSizeBytes": raw}],
            "highWaterMark": 98,
            "lowWaterMark": 80,
            "diskPoolState": "UP",
            "usableSizeBytes": raw,
            "availableSpaceBytes": free,
            "usedCapacityBytes": used,
        },
        "relationships": {
            "storageServers": {"data": [{"type": "storageServer", "id": "PureDisk:primary-01"}]}
        },
    }


def _unit(name: str, pool: str | None, *, cloud: bool = False, **extra: Any) -> dict[str, Any]:
    unit: dict[str, Any] = {
        "type": "storageUnit",
        "id": name,
        "attributes": {
            "name": name,
            "storageType": "Disk",
            "storageSubType": "DiskPool" if pool else "BasicDisk",
            "isCloudSTU": cloud,
            "mediaServerDetails": [{"name": "media-01"}, {"name": "primary-01"}],
            **extra,
        },
    }
    if pool:
        unit["relationships"] = {"diskPool": {"data": {"type": "diskPool", "id": pool}}}
    return unit


def _fake(pools: list[dict[str, Any]], units: list[dict[str, Any]]) -> NetBackupClient:
    def paged(items: list[dict[str, Any]]) -> Any:
        def call(*, page_limit: int, page_offset: int) -> dict[str, Any]:
            return {"data": items[page_offset : page_offset + page_limit]}

        return call

    storage = SimpleNamespace(list_disk_pools=paged(pools), list_storage_units=paged(units))
    return cast(NetBackupClient, SimpleNamespace(raw=SimpleNamespace(storage=storage)))


def test_an_msdp_pool_with_its_media_servers() -> None:
    fake = _fake(
        [_pool("dp-01", 8 * 1024**4, 7 * 1024**4, 65 * 1024**3)],
        [_unit("stu-01", "PureDisk:dp-01"), _unit("stu-01b", "PureDisk:dp-01")],
    )
    (pool,) = netbackup_storage(fake)
    assert (pool.product_key, pool.name, pool.kind, pool.product_type) == (
        "PureDisk:dp-01",
        "dp-01",
        "dedupe",
        "MSDP",
    )
    assert pool.hosts == ["media-01", "primary-01"]
    assert pool.server == "primary-01"
    assert (pool.capacity_bytes, pool.free_bytes, pool.used_bytes) == (
        8 * 1024**4,
        7 * 1024**4,
        65 * 1024**3,
    )
    assert pool.attributes["storage_units"] == ["stu-01", "stu-01b"]
    assert pool.attributes["high_water_mark"] == 98


def test_msdp_on_cloud_storage_has_no_capacity() -> None:
    fake = _fake(
        [_pool("dp-cloud", UNLIMITED, UNLIMITED - 7, 7_247_636_077)],
        [_unit("msdp-c", "PureDisk:dp-cloud", cloud=True)],
    )
    (pool,) = netbackup_storage(fake)
    assert (pool.kind, pool.product_type) == ("cloud", "MSDP-C")
    assert pool.capacity_bytes is None and pool.free_bytes is None
    assert pool.used_bytes == 7_247_636_077


def test_a_basic_disk_unit_is_its_own_target_and_tape_is_left_out() -> None:
    basic = _unit(
        "basic-01",
        None,
        totalCapacityBytes=100,
        freeCapacityBytes=40,
        usedCapacityBytes=60,
        path="/backups",
    )
    tape = {"type": "storageUnit", "id": "tape-01", "attributes": {"storageType": "Media_Manager"}}
    (target,) = netbackup_storage(_fake([], [basic, tape]))
    assert (target.product_key, target.kind, target.product_type) == (
        "storage-unit:basic-01",
        "disk",
        "BasicDisk",
    )
    assert (target.capacity_bytes, target.free_bytes, target.used_bytes) == (100, 40, 60)


def test_every_page_is_read() -> None:
    pools = [_pool(f"dp-{n:03}", 100, 50, 50) for n in range(PAGE + 5)]
    assert len(netbackup_storage(_fake(pools, []))) == PAGE + 5
