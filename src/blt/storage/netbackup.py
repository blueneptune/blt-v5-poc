"""A NetBackup primary's storage: every disk pool, and every disk storage
unit that has no pool (BasicDisk), with its media servers and use.

primary -> media server -> storage server -> disk pool -> storage unit
(docs/storage.md). The disk pool is the target - it is what has a
capacity; its storage units are views onto it, so counting them would
count the same disk several times. A pool's media servers are those of
the storage units that write to it; its storage server comes from the
pool's own relationships.

| What | From |
|---|---|
| capacity, free, used (bytes) | GET /storage/disk-pools |
| media servers, cloud or not | GET /storage/storage-units |

NetBackup gives 2^53 bytes as the size of storage with no fixed size (an
MSDP cloud volume): that is no capacity, not 8 PiB.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from netbackup_sdk import NetBackupClient

from blt.schemas import StorageIn, StorageKind

PAGE = 100
# What NetBackup reports as the size of storage without one.
UNLIMITED = 2**53


def _all(list_call: Callable[..., Any]) -> Iterator[dict[str, Any]]:
    """Every item of an offset-paged JSON:API list."""
    offset = 0
    while True:
        answer = list_call(page_limit=PAGE, page_offset=offset) or {}
        data = answer.get("data") or []
        yield from data
        if len(data) < PAGE:
            return
        offset += PAGE


def _size(value: Any) -> int | None:
    if not isinstance(value, int) or value < 0 or value >= UNLIMITED:
        return None
    return value


def _kind(category: str | None, stype: str | None, cloud: bool) -> StorageKind:
    # Spelled BASIC_DISK in one answer and BasicDisk in another.
    category = (category or "").upper().replace("_", "")
    stype = (stype or "").lower()
    if cloud or category == "CLOUD":
        return "cloud"
    if category == "MSDP" or "datadomain" in stype or stype == "puredisk":
        return "dedupe"
    if category in ("ADVANCEDDISK", "BASICDISK") or stype in ("advanceddisk", "basicdisk"):
        return "disk"
    return "other"


def _product_type(category: str | None, stype: str | None, cloud: bool) -> str:
    """NetBackup's words for it: MSDP, MSDP-C (MSDP on cloud storage),
    AdvancedDisk, BasicDisk, DataDomain, ..."""
    if (category or "").upper() == "MSDP" and cloud:
        return "MSDP-C"
    return category or stype or "unknown"


def _after_colon(value: str) -> str:
    return value.split(":", 1)[1] if ":" in value else value


def netbackup_storage(nbu: NetBackupClient) -> list[StorageIn]:
    units = [u for u in _all(nbu.raw.storage.list_storage_units)]
    by_pool: dict[str, list[dict[str, Any]]] = {}
    targets = []
    for unit in units:
        pool = ((unit.get("relationships") or {}).get("diskPool") or {}).get("data") or {}
        if pool.get("id"):
            by_pool.setdefault(str(pool["id"]), []).append(unit)
        elif (unit.get("attributes") or {}).get("storageType") == "Disk":
            targets.append(_unit_target(unit))

    for pool in _all(nbu.raw.storage.list_disk_pools):
        attributes = pool.get("attributes") or {}
        pool_units = by_pool.get(str(pool["id"]), [])
        hosts = sorted(
            {
                str(server.get("name"))
                for unit in pool_units
                for server in (unit.get("attributes") or {}).get("mediaServerDetails") or []
                if server.get("name")
            }
        )
        servers = [
            _after_colon(str(s.get("id")))
            for s in ((pool.get("relationships") or {}).get("storageServers") or {}).get("data")
            or []
        ]
        cloud = any((u.get("attributes") or {}).get("isCloudSTU") for u in pool_units) or (
            attributes.get("usableSizeBytes", 0) >= UNLIMITED
        )
        category, stype = attributes.get("storageCategory"), attributes.get("sType")
        capacity = _size(attributes.get("usableSizeBytes"))
        targets.append(
            StorageIn(
                product_key=str(pool["id"]),
                name=str(attributes.get("name") or _after_colon(str(pool["id"]))),
                kind=_kind(category, stype, cloud),
                product_type=_product_type(category, stype, cloud),
                hosts=hosts,
                server=", ".join(servers) or None,
                status=attributes.get("diskPoolState"),
                capacity_bytes=capacity,
                free_bytes=_size(attributes.get("availableSpaceBytes")) if capacity else None,
                used_bytes=_size(attributes.get("usedCapacityBytes")),
                attributes={
                    "storage_units": sorted(str(u.get("id")) for u in pool_units),
                    "volumes": [
                        {
                            "name": v.get("name"),
                            "state": v.get("state"),
                            "raw_bytes": _size(v.get("rawSizeBytes")),
                            "free_bytes": _size(v.get("freeSizeBytes")),
                        }
                        for v in attributes.get("diskVolumes") or []
                    ],
                    "high_water_mark": attributes.get("highWaterMark"),
                    "low_water_mark": attributes.get("lowWaterMark"),
                    "storage_server_type": stype,
                    "read_only": attributes.get("readOnly"),
                    "worm_capable": attributes.get("wormCapable"),
                },
            )
        )
    return targets


def _unit_target(unit: dict[str, Any]) -> StorageIn:
    """A disk storage unit with no disk pool (BasicDisk: a directory on
    one media server)."""
    attributes = unit.get("attributes") or {}
    cloud = bool(attributes.get("isCloudSTU"))
    category = attributes.get("storageCategory") or attributes.get("storageSubType")
    capacity = _size(attributes.get("totalCapacityBytes"))
    return StorageIn(
        product_key=f"storage-unit:{unit['id']}",
        name=str(attributes.get("name") or unit["id"]),
        kind=_kind(category, attributes.get("storageServerType"), cloud),
        product_type=attributes.get("storageSubType") or category,
        hosts=sorted(
            str(s.get("name")) for s in attributes.get("mediaServerDetails") or [] if s.get("name")
        ),
        capacity_bytes=capacity,
        free_bytes=_size(attributes.get("freeCapacityBytes")) if capacity else None,
        used_bytes=_size(attributes.get("usedCapacityBytes")),
        attributes={"storage_units": [str(unit["id"])], "path": attributes.get("path")},
    )
