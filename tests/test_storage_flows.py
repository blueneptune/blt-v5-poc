"""The Storage page's Sankey (blt.api.storage_flows): NetBackup's
backups into each pool, and copies between pools - on one primary, and
replicated between two primaries that replicate to each other."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

GIB = 1024**3
NOW = datetime.now(UTC)


def pool(key: str, units: list[str], *, used: int, capacity: int = 100 * GIB) -> dict[str, Any]:
    return {
        "product_key": key,
        "name": key,
        "kind": "dedupe",
        "capacity_bytes": capacity,
        "used_bytes": used,
        "free_bytes": capacity - used,
        "attributes": {"storage_units": units},
    }


def job(job_id: int, job_type: str, **extra: Any) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "job_type": job_type,
        "state": "DONE",
        "status": 0,
        "is_active": False,
        "start_time": (NOW - timedelta(hours=3)).isoformat(),
        "end_time": (NOW - timedelta(hours=2)).isoformat(),
        "last_update_time": (NOW - timedelta(hours=2)).isoformat(),
        **extra,
    }


def post(http: TestClient, primary: str, path: str, body: dict[str, Any]) -> None:
    response = http.post(f"/v1/sources/netbackup/{primary}/{path}", json=body)
    assert response.status_code == 200, response.text


def seed(http: TestClient) -> None:
    at = (NOW - timedelta(minutes=5)).isoformat()
    post(
        http,
        "nbu-a",
        "storage",
        {
            "collected_at": at,
            "targets": [
                pool("msdp-a", ["stu-a"], used=96 * GIB),
                pool("tape-a", ["stu-tape"], used=0),
            ],
        },
    )
    post(
        http,
        "nbu-b",
        "storage",
        {
            "collected_at": at,
            "targets": [
                pool("msdp-b", ["stu-b"], used=30 * GIB),
                pool("idle-b", ["stu-idle"], used=GIB),
            ],
        },
    )
    kb = GIB // 1024
    post(
        http,
        "nbu-a",
        "jobs",
        {
            "collected_at": at,
            "jobs": [
                job(1, "BACKUP", storage_unit="stu-a", kilobytes_transferred=10 * kb),
                # A VMware parent with its two children: only the children count.
                job(
                    2, "BACKUP", storage_unit="stu-a", kilobytes_transferred=8 * kb, parent_job_id=2
                ),
                job(
                    3, "BACKUP", storage_unit="stu-a", kilobytes_transferred=5 * kb, parent_job_id=2
                ),
                job(
                    4, "BACKUP", storage_unit="stu-a", kilobytes_transferred=3 * kb, parent_job_id=2
                ),
                # Failed, too old, still running: none count.
                job(5, "BACKUP", storage_unit="stu-a", kilobytes_transferred=kb, status=13),
                job(
                    6,
                    "BACKUP",
                    storage_unit="stu-a",
                    kilobytes_transferred=kb,
                    end_time=(NOW - timedelta(days=9)).isoformat(),
                ),
                job(
                    7,
                    "BACKUP",
                    storage_unit="stu-a",
                    kilobytes_transferred=kb,
                    state="ACTIVE",
                    status=None,
                    is_active=True,
                    end_time=None,
                ),
                job(
                    8,
                    "DUPLICATE",
                    source_storage_unit="stu-a",
                    storage_unit="stu-tape",
                    policy_name="slp-gold",
                    kilobytes_transferred=4 * kb,
                    backup_id="c1_1",
                ),
                job(
                    9,
                    "REPLICATE",
                    source_storage_unit="stu-a",
                    storage_unit="nbu-b-target",
                    policy_name="slp-gold",
                    kilobytes_transferred=6 * kb,
                    backup_id="c1_2",
                ),
                # Its import on nbu-b is not collected (yet).
                job(
                    10,
                    "REPLICATE",
                    source_storage_unit="stu-a",
                    storage_unit="nbu-b-target",
                    kilobytes_transferred=2 * kb,
                    backup_id="c1_3",
                ),
            ],
        },
    )
    post(
        http,
        "nbu-b",
        "jobs",
        {
            "collected_at": at,
            "jobs": [
                job(1, "BACKUP", storage_unit="stu-b", kilobytes_transferred=7 * kb),
                job(
                    2,
                    "REPLICA_IMPORT",
                    storage_unit="stu-b",
                    backup_id="c1_2",
                    kilobytes_transferred=kb,
                ),
                # and nbu-b replicates back to nbu-a: no loop, pools are drawn once.
                job(
                    3,
                    "REPLICATE",
                    source_storage_unit="stu-b",
                    storage_unit="nbu-a-target",
                    policy_name="slp-back",
                    kilobytes_transferred=kb,
                    backup_id="c2_1",
                ),
            ],
        },
    )
    post(
        http,
        "nbu-a",
        "jobs",
        {
            "collected_at": at,
            "jobs": [
                job(11, "IMPORT", storage_unit="stu-a", backup_id="c2_1", kilobytes_transferred=kb),
            ],
        },
    )


def test_backups_and_copies_land_in_their_pools(http: TestClient) -> None:
    seed(http)
    flows = http.get("/v1/storage/flows", params={"days": 7}).json()
    names = {t["id"]: t["name"] for t in flows["targets"]}
    nodes = {n["id"]: n for n in flows["nodes"]}

    def name(node_id: str) -> str:
        node = nodes[node_id]
        return names[node["storage_id"]] if node["storage_id"] else node["label"]

    got = {
        (name(link["source"]), name(link["target"]), link["kind"]): (
            link["bytes"] // GIB,
            link["jobs"],
        )
        for link in flows["links"]
    }
    assert got == {
        ("Backups on nbu-a", "msdp-a", "backup"): (18, 3),
        ("Backups on nbu-b", "msdp-b", "backup"): (7, 1),
        ("Copies from msdp-a", "tape-a", "duplicate"): (4, 1),
        ("Copies from msdp-a", "msdp-b", "replicate"): (6, 1),
        ("Copies from msdp-a", "nbu-b-target (replica, import not seen)", "replicate"): (2, 1),
        ("Copies from msdp-b", "msdp-a", "replicate"): (1, 1),
    }
    assert flows["unmatched_replications"] == 1
    # Each pool is drawn once, on the right, whichever way data reaches it.
    pools = [n for n in flows["nodes"] if n["storage_id"]]
    assert all(n["column"] == 1 for n in pools)
    assert len({n["storage_id"] for n in pools}) == len(pools)
    # Every pool comes with its use - the idle one too.
    assert sorted(names.values()) == ["idle-b", "msdp-a", "msdp-b", "tape-a"]
    full = next(t for t in flows["targets"] if t["name"] == "msdp-a")
    assert full["latest"]["used_bytes"] == 96 * GIB


def test_one_primary_goes_pool_to_slp_to_pool(http: TestClient) -> None:
    seed(http)
    flows = http.get("/v1/storage/flows", params={"primary": "nbu-a"}).json()
    assert flows["primary"] == "nbu-a"
    assert flows["primaries"] == ["nbu-a", "nbu-b"]
    nodes = {n["id"]: n for n in flows["nodes"]}

    def at(node_id: str) -> tuple[int, str]:
        return nodes[node_id]["column"], nodes[node_id]["label"]

    got = {
        (at(link["source"]), at(link["target"]), link["kind"]): link["bytes"] // GIB
        for link in flows["links"]
    }
    assert got == {
        ((0, "msdp-a"), (1, "slp-gold"), "duplicate"): 4,
        ((0, "msdp-a"), (1, "slp-gold"), "replicate"): 6,
        ((1, "slp-gold"), (2, "tape-a"), "duplicate"): 4,
        ((1, "slp-gold"), (2, "msdp-b"), "replicate"): 6,
        # A copy with no policy name was not made by an SLP.
        ((0, "msdp-a"), (1, "Not from an SLP"), "replicate"): 2,
        ((1, "Not from an SLP"), (2, "nbu-b-target (replica, import not seen)"), "replicate"): 2,
    }
    # Backups, and the other primary's copies, are not in it.
    assert all(n["source_name"] == "nbu-a" or n["column"] == 2 for n in nodes.values())
    assert flows["unmatched_replications"] == 1
    # Each way end to end, for clicking one: pool -> SLP -> pool.
    ends = {tuple(at(n)[1] for n in path["nodes"]) for path in flows["paths"]}
    assert ("msdp-a", "slp-gold", "msdp-b") in ends
    assert all(len(path["nodes"]) == 3 for path in flows["paths"])


def test_a_pool_both_read_and_written_on_one_primary_is_on_both_sides(http: TestClient) -> None:
    seed(http)
    flows = http.get("/v1/storage/flows", params={"primary": "nbu-b"}).json()
    pools = sorted((n["column"], n["label"]) for n in flows["nodes"] if n["storage_id"])
    assert pools == [(0, "msdp-b"), (2, "msdp-a")]
    assert http.get("/v1/storage/flows", params={"primary": "nobody"}).json()["links"] == []


def test_the_window_decides_what_counts(http: TestClient) -> None:
    seed(http)
    flows = http.get("/v1/storage/flows", params={"days": 30}).json()
    backups = next(link for link in flows["links"] if link["source"] == "backups:nbu-a")
    assert backups["jobs"] == 4  # the 9-day-old one too
    assert http.get("/v1/storage/flows", params={"days": 0}).status_code == 422


def test_no_netbackup_means_an_empty_picture(http: TestClient) -> None:
    flows = http.get("/v1/storage/flows").json()
    assert flows["nodes"] == [] and flows["links"] == [] and flows["targets"] == []


def test_a_copy_naming_no_source_is_traced_by_its_slp_then_its_backup(http: TestClient) -> None:
    at = (NOW - timedelta(minutes=5)).isoformat()
    kb = GIB // 1024
    post(
        http,
        "nbu-a",
        "storage",
        {
            "collected_at": at,
            "targets": [
                pool("msdp-a", ["stu-a"], used=50 * GIB),
                pool("msdp-a2", ["stu-a2"], used=10 * GIB),
                pool("tape-a", ["stu-tape"], used=0),
            ],
        },
    )
    post(
        http,
        "nbu-a",
        "slps",
        {
            "collected_at": at,
            "slps": [
                {
                    "name": "slp-gold",
                    "operations": [
                        {"index": 1, "type": "BACKUP", "storage": "stu-a"},
                        {"index": 2, "type": "DUPLICATION", "storage": "stu-a2", "source_index": 1},
                        # Tape is made from the second copy, not the backup.
                        {
                            "index": 3,
                            "type": "DUPLICATION",
                            "storage": "stu-tape",
                            "source_index": 2,
                        },
                    ],
                }
            ],
        },
    )
    post(
        http,
        "nbu-a",
        "jobs",
        {
            "collected_at": at,
            "jobs": [
                job(1, "BACKUP", storage_unit="stu-a", backup_id="img1", kilobytes_transferred=kb),
                # Its SLP says: tape reads the copy on stu-a2.
                job(
                    2,
                    "DUPLICATE",
                    policy_name="slp-gold",
                    storage_unit="stu-tape",
                    backup_id="img1",
                    kilobytes_transferred=3 * kb,
                ),
                # No SLP known: where its image was backed up to.
                job(
                    3,
                    "DUPLICATE",
                    policy_name="slp-not-collected",
                    storage_unit="stu-a2",
                    backup_id="img1",
                    kilobytes_transferred=2 * kb,
                ),
                # Nothing tells.
                job(4, "DUPLICATE", storage_unit="stu-tape", kilobytes_transferred=kb),
            ],
        },
    )
    flows = http.get("/v1/storage/flows", params={"primary": "nbu-a"}).json()
    label = {n["id"]: n["label"] for n in flows["nodes"]}
    got = {tuple(label[n] for n in path["nodes"]): path["bytes"] // GIB for path in flows["paths"]}
    assert got == {
        ("msdp-a2", "slp-gold", "tape-a"): 3,
        ("msdp-a", "slp-not-collected", "msdp-a2"): 2,
        ("unknown", "Not from an SLP", "tape-a"): 1,
    }
    assert flows["unknown_sources"] == 1
    # The links are the paths' steps, summed.
    assert sum(link["bytes"] for link in flows["links"]) // GIB == 2 * (3 + 2 + 1)


def test_slps_are_replaced_as_a_whole(http: TestClient) -> None:
    at = NOW - timedelta(minutes=5)

    def send(when: datetime, names: list[str]) -> None:
        body = {"collected_at": when.isoformat(), "slps": [{"name": n} for n in names]}
        post(http, "nbu-a", "slps", body)

    send(at, ["one", "two"])
    send(at + timedelta(minutes=1), ["two", "three"])
    send(at - timedelta(hours=1), ["stale"])  # older than what is held: left alone
    held = http.get("/v1/sources/netbackup/nbu-a/slps").json()
    assert [s["name"] for s in held] == ["three", "two"]
    assert http.get("/v1/sources/netbackup/nobody/slps").status_code == 404
