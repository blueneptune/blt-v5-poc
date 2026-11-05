"""A CommCell's storage: every storage pool, with the MediaAgents that
write to it, its capacity and its use.

CommServe -> MediaAgent -> storage pool (docs/storage.md). The numbers
come from GET /StoragePool, which lists every pool of every type with
its capacity, free space and size on disk, in MB. What sort of pool it
is comes from the V4 lists (/V4/Storage/Disk, /Cloud, /Tape, /Local),
which word it ("DEDUPLICATION"); which MediaAgents write to it from each
MediaAgent's associations. A disk pool's own page adds its dedupe
savings and its mount paths.

A pool is shared by every MediaAgent that can reach it, so it is one
target with several hosts, not one per MediaAgent: counted once.
"""

from __future__ import annotations

from typing import Any

from commvault_sdk import CommvaultClient
from loguru import logger
from sdk_primer import SDKError

from blt.schemas import StorageIn, StorageKind

MB = 1024 * 1024

# Which V4 list a pool is in, and the key its answer lists them under.
_LISTS: dict[str, tuple[str, str, StorageKind]] = {
    "disk": ("disk_storage", "diskStorage", "disk"),
    "cloud": ("cloud_storage", "cloudStorage", "cloud"),
    "tape": ("tape_storage", "tapeStorage", "tape"),
    "local": ("local_storage", "localStorage", "disk"),
}


def _bytes(mb: Any) -> int | None:
    try:
        value = int(mb)
    except (TypeError, ValueError):
        return None
    return value * MB if value >= 0 else None


def _kinds(cv: CommvaultClient, problems: list[str]) -> dict[int, tuple[StorageKind, str | None]]:
    """Pool id -> (kind, Commvault's own word for its type)."""
    kinds: dict[int, tuple[StorageKind, str | None]] = {}
    for label, (area, key, kind) in _LISTS.items():
        try:
            answer = getattr(getattr(cv.raw, area), f"list_storage_{label}")()
        except SDKError as exc:
            problems.append(f"{label} storage list: {exc}")
            continue
        for item in (answer or {}).get(key) or []:
            # Local lists a disk pool again: the disk list's word stands.
            if item.get("id") is None or (label == "local" and int(item["id"]) in kinds):
                continue
            pool_type = item.get("storagePoolType")
            found: StorageKind = "dedupe" if pool_type == "DEDUPLICATION" else kind
            kinds[int(item["id"])] = (found, pool_type)
    return kinds


def _hosts(cv: CommvaultClient, problems: list[str]) -> dict[int, list[str]]:
    """Pool id -> the MediaAgents associated with it."""
    hosts: dict[int, list[str]] = {}
    for agent in (cv.raw.media_agent.list_media_agent() or {}).get("mediaAgents") or []:
        try:
            answer = cv.raw.media_agent.get_media_agent_associations(agent["id"])
        except SDKError as exc:
            problems.append(f"MediaAgent {agent.get('name')}: {exc}")
            continue
        for association in (answer or {}).get("associations") or []:
            if association.get("entityType") == "Storage" and association.get("id") is not None:
                hosts.setdefault(int(association["id"]), []).append(str(agent.get("name")))
    return {pool: sorted(set(names)) for pool, names in hosts.items()}


def _disk_detail(cv: CommvaultClient, pool_id: int) -> dict[str, Any]:
    try:
        return cv.raw.disk_storage.get_storage_disk(pool_id) or {}
    except SDKError as exc:
        logger.warning("Storage pool {}: no detail ({})", pool_id, exc)
        return {}


def commvault_storage(cv: CommvaultClient, problems: list[str] | None = None) -> list[StorageIn]:
    """Every storage pool on the CommCell. A list that cannot be read is
    named in `problems`; the pools still come from /StoragePool."""
    if problems is None:
        problems = []
    kinds = _kinds(cv, problems)
    hosts = _hosts(cv, problems)
    targets = []
    for pool in (cv.raw.storage.list_storage_pool() or {}).get("storagePoolList") or []:
        entity = pool.get("storagePoolEntity") or {}
        if entity.get("storagePoolId") is None:
            continue
        pool_id = int(entity["storagePoolId"])
        kind, pool_type = kinds.get(pool_id, ("other", None))
        attributes: dict[str, Any] = {
            "usable_free_bytes": _bytes(pool.get("totalUsableFreeSpace")),
            "library_ids": [lib.get("libraryId") for lib in pool.get("libraryList") or []],
            "storage_policy": (pool.get("storagePolicyEntity") or {}).get("storagePolicyName"),
            "worm": bool(pool.get("wormStoragePoolFlag"))
            if "wormStoragePoolFlag" in pool
            else None,
            "archive": bool(pool.get("isArchiveStorage")),
        }
        physical = _bytes(pool.get("sizeOnDisk"))
        logical = ratio = None
        if kind in ("disk", "dedupe"):
            detail = _disk_detail(cv, pool_id)
            general = detail.get("general") or {}
            savings = general.get("dedupeSavingsPercent")
            if isinstance(savings, int | float) and 0 <= savings < 100 and physical:
                ratio = round(100 / (100 - savings), 2)
                logical = int(physical * ratio)
            attributes["mount_paths"] = [
                {"name": loc.get("name"), "status": loc.get("status")}
                for loc in detail.get("backupLocations") or []
            ]
            attributes["plans"] = [p.get("planName") for p in detail.get("associatedPlans") or []]
            attributes["dedupe_savings_percent"] = savings
        capacity = _bytes(pool.get("totalCapacity"))
        free = _bytes(pool.get("totalFreeSpace"))
        targets.append(
            StorageIn(
                product_key=str(pool_id),
                name=str(entity.get("storagePoolName") or pool_id),
                kind=kind,
                product_type=pool_type,
                hosts=hosts.get(pool_id, []),
                status=pool.get("status"),
                capacity_bytes=capacity,
                free_bytes=free,
                used_bytes=capacity - free if capacity is not None and free is not None else None,
                physical_bytes=physical,
                logical_bytes=logical,
                dedupe_ratio=ratio,
                attributes=attributes,
            )
        )
    return targets
