"""NetBackup's inventory: the collector (blt.netbackup.assets) against
respx-faked answers shaped from the 10.2 spec (assetservice.yaml's
asset-service listings, config_policies.yaml's generic policy schema,
GET /config/hosts), then through the API into netbackup.source_object
and the cross-source /v1/assets."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from netbackup_sdk import NetBackupClient
from sdk_primer import APIClient

import blt.netbackup.assets as assets
from blt.netbackup.assets import (
    active_protection,
    iter_netbackup_inventory,
    run_netbackup_inventory,
)
from blt.netbackup.schemas import NetBackupJobIn, NetBackupObjectIn, policy_family
from blt.netbackup.store import NetBackupStore

URL = "https://nbu.test:1556/netbackup"
BASE = "/v1/sources/netbackup/nbu-lab"
RECENT = datetime.now(UTC) - timedelta(hours=3)


def _iso(when: datetime) -> str:
    return when.isoformat().replace("+00:00", "Z")


def _listing(items: list[dict[str, Any]], *, has_next: bool = False) -> dict[str, Any]:
    return {
        "data": items,
        "meta": {"pagination": {"offset": 0, "limit": 100, "hasNext": has_next, "first": 0}},
    }


def _policy(name: str, policy_type: str, active: bool, clients: list[dict[str, Any]]) -> dict:
    return {
        "type": "policy",
        "id": name,
        "attributes": {
            "policy": {
                "policyName": name,
                "policyType": policy_type,
                "policyAttributes": {"active": active},
                "clients": clients,
                "schedules": [],
                "backupSelections": {"selections": ["ALL_LOCAL_DRIVES"]},
            }
        },
    }


def _common(name: str, workload: str, protection: list[dict[str, Any]] | None = None) -> dict:
    common: dict[str, Any] = {
        "displayName": name,
        "workloadType": workload,
        "providerGeneratedId": f"{workload}_{name}",
        "protectionCapabilities": {"isProtectable": "YES", "isRecoverable": "YES"},
        "detection": {"detectionMethod": "DISCOVERY", "lastDiscoveredTime": _iso(RECENT)},
    }
    if protection is not None:
        common["activeProtection"] = {"protectionDetailsList": protection}
    return common


def _vm(asset_id: str, name: str, protection: list[dict[str, Any]] | None = None, **extra: Any):
    return {
        "type": "vmwareAsset",
        "id": asset_id,
        "attributes": {
            "assetType": "vm",
            "commonAssetAttributes": _common(name, "VMware", protection),
            "vCenter": "vc01.example.com",
            "datacenter": "dc1",
            "cluster": "cl1",
            "host": "esx01.example.com",
            "instanceUuid": f"5010-{asset_id}",
            "uuid": f"4210-{asset_id}",
            "powerState": "poweredOn",
            "template": "false",
            **extra,
        },
    }


def _sql(asset_id: str, asset_type: str, protection: list | None = None, **attributes: Any):
    name = attributes.get("databaseName") or attributes.get("instanceName")
    return {
        "type": "mssqlAsset",
        "id": asset_id,
        "attributes": {
            "assetType": asset_type,
            "commonAssetAttributes": _common(name, "MSSQL", protection),
            **attributes,
        },
    }


POLICIES = [
    _policy("fs-daily", "Standard", True, [{"hostName": "sql01.example.com", "OS": "Windows"}]),
    _policy("fs-old", "MS-Windows", False, [{"hostName": "app02"}]),
    _policy("vm-manual", "VMware", True, [{"hostName": "web02"}]),
    _policy(
        "sql-prod",
        "MS-SQL-Server",
        True,
        [{"hostName": "sql01", "instanceName": "MSSQLSERVER", "databaseName": "hr"}],
    ),
    _policy("ora-prod", "Oracle", True, [{"hostName": "ora01"}]),
]

HOSTS = {
    "hosts": [
        {
            "hostName": "nbu01",
            "uuid": "aaaa-1",
            "nbuHostType": "PRIMARY_SERVER",
            "osType": "UNIX",
            "hostMappings": [],
        },
        {
            "hostName": "sql01",
            "uuid": "aaaa-2",
            "nbuHostType": "CLIENT",
            "osType": "WINDOWS",
            "hostMappings": [{"mappedHostName": "sql01.example.com", "isApproved": True}],
        },
        {"hostName": "app02", "uuid": "aaaa-3", "nbuHostType": "CLIENT", "osType": "UNIX"},
    ]
}


def _db(asset_id: str, name: str, protection: list | None = None) -> dict[str, Any]:
    """A database on sql01's default instance."""
    return _sql(
        asset_id,
        "DATABASE",
        protection,
        databaseName=name,
        instanceName="MSSQLSERVER",
        clientName="sql01",
    )


MSSQL = [
    _sql("inst-1", "INSTANCE", None, instanceName="MSSQLSERVER", clientName="sql01"),
    _db(
        "db-sales",
        "sales",
        [{"protectionPlanName": "Gold", "lastSuccessfulBackupTime": _iso(RECENT), "jobId": 812}],
    ),
    _db("db-hr", "hr", []),
    _db("db-crm", "crm"),
    _db("db-tmp", "tempdb"),
    _sql("ag-1", "AVAILABILITY_GROUP", None, availabilityGroupName="ag1"),
]

VMS = [
    _vm(
        "vm-1",
        "web01",
        [{"policyName": "vm-gold", "lastSuccessfulBackupTime": _iso(RECENT), "jobId": 900}],
        hostName="web01.example.com",
    ),
    _vm("vm-2", "web02"),
    _vm("vm-3", "test03", dnsName="None"),
]


def _fake_primary(vms: list[dict[str, Any]] = VMS, *, hosts_status: int = 200) -> respx.Router:
    router = respx.mock(base_url=URL, assert_all_called=False)
    router.get("/config/policies").respond(json=_listing(POLICIES))
    for policy in POLICIES:
        router.get(f"/config/policies/{policy['id']}").respond(json={"data": policy})
    router.get("/config/hosts").respond(hosts_status, json=HOSTS if hosts_status == 200 else {})
    router.get("/asset-service/workloads/mssql/assets").respond(json=_listing(MSSQL))

    def vmware(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("page[offset]", 0))
        limit = int(request.url.params.get("page[limit]", 100))
        page = vms[offset : offset + limit]
        return httpx.Response(200, json=_listing(page, has_next=offset + limit < len(vms)))

    router.get("/asset-service/workloads/vmware/assets").mock(side_effect=vmware)
    router.get("/asset-service/workloads/rhv/assets").respond(
        404, json={"errorCode": 404, "errorMessage": "workload not found"}
    )
    return router


def _read(**fake: Any) -> tuple[list[list[NetBackupObjectIn]], list[str], respx.Router]:
    problems: list[str] = []
    with _fake_primary(**fake) as router, NetBackupClient(URL, api_key="k") as nbu:
        batches = list(iter_netbackup_inventory(nbu, problems))
    return batches, problems, router


@pytest.fixture(autouse=True)
def _small_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    # Two to a page, so three VMs take two pages.
    monkeypatch.setattr(assets, "PAGE", 2)


def test_policy_types_by_what_their_clients_are() -> None:
    assert policy_family("MS-Windows") == policy_family("MS_WINDOWS") == "host"
    assert policy_family("Standard") == "host"
    assert policy_family("VMWARE") == policy_family("Hyper-V") == "vm"
    assert policy_family("MS-SQL-Server") == "mssql"
    assert policy_family("Oracle") == policy_family(None) == "other"


def test_active_protection_takes_plans_policies_and_the_newest_backup() -> None:
    older = RECENT - timedelta(days=2)
    found = active_protection(
        {
            "commonAssetAttributes": {
                "activeProtection": {
                    "protectionDetailsList": [
                        {"policyName": "p1", "lastSuccessfulBackupTime": _iso(older), "jobId": 1},
                        {"protectionPlanName": "Gold", "policyName": "p2",
                         "lastSuccessfulBackupTime": _iso(RECENT), "jobId": 2},
                    ]
                }
            }
        }
    )  # fmt: skip
    assert found.plans == ["p1", "Gold"]
    assert found.last_backup_at == RECENT
    assert found.job_id == 2
    assert active_protection({}).plans == []


def test_the_inventory_reads_hosts_sql_server_and_vms_parents_first() -> None:
    batches, problems, router = _read()
    assert problems == []
    hosts, instances, databases, vmware, rhv = batches
    assert [b[0].kind for b in (hosts, instances, databases, vmware)] == [
        "host",
        "instance",
        "database",
        "vm",
    ]
    assert rhv == []  # no RHV workload on this primary: skipped, not a problem
    # An Oracle policy is never read in full.
    assert not router.get(f"{URL}/config/policies/ora-prod").called

    by_host = {h.source_key: h for h in hosts}
    # The policy names sql01 by its FQDN; /config/hosts maps that to sql01.
    assert set(by_host) == {"nbu01", "sql01", "app02"}
    sql01 = by_host["sql01"]
    assert (sql01.protection, sql01.plan, sql01.app_type) == ("protected", "fs-daily", "Standard")
    assert sql01.attributes["identifiers"]["aliases"] == ["sql01.example.com"]
    assert sql01.attributes["identifiers"]["host_uuid"] == "aaaa-2"
    # Only in a deactivated policy: not protected.
    app02 = by_host["app02"]
    assert app02.protection == "unprotected"
    assert app02.attributes["inactive_policies"] == ["fs-old"]
    assert by_host["nbu01"].protection == "unprotected"

    (instance,) = instances
    assert (instance.parent_kind, instance.parent_key, instance.name) == (
        "host",
        "sql01",
        "MSSQLSERVER",
    )
    # Its databases are protected one by one, not the instance as a whole.
    assert instance.protection is None

    dbs = {d.name: d for d in databases}
    assert set(dbs) == {"sales", "hr", "crm", "tempdb"}  # the availability group is not a row
    sales = dbs["sales"]
    assert (sales.protection, sales.plan, sales.last_backup_job_id) == ("protected", "Gold", 812)
    assert sales.attributes["protection_from"] == "asset service"
    assert sales.parent_key == "inst-1" and sales.location == "sql01 / MSSQLSERVER"
    hr = dbs["hr"]
    assert (hr.protection, hr.plan, hr.attributes["protection_from"]) == (
        "protected",
        "sql-prod",
        "policy",
    )
    assert dbs["crm"].protection == "unprotected"
    assert dbs["tempdb"].protection == "excluded"

    vms = {v.name: v for v in vmware}
    assert set(vms) == {"web01", "web02", "test03"}  # three VMs, two pages
    web01 = vms["web01"]
    assert (web01.source_key, web01.protection, web01.plan) == ("vm-1", "protected", "vm-gold")
    assert web01.last_backup_job_id == 900 and web01.last_backup_at is not None
    assert web01.location == "vc01.example.com / dc1 / cl1"
    ids = web01.attributes["identifiers"]
    assert (ids["instance_uuid"], ids["bios_uuid"], ids["host_name"]) == (
        "5010-vm-1",
        "4210-vm-1",
        "web01.example.com",
    )
    # Named as a client of an active VMware policy.
    assert (vms["web02"].protection, vms["web02"].plan) == ("protected", "vm-manual")
    assert vms["test03"].protection == "unprotected"
    assert vms["test03"].attributes["identifiers"]["dns_name"] is None


def test_an_area_netbackup_refuses_is_a_problem_and_the_rest_is_read() -> None:
    batches, problems, _ = _read(hosts_status=403)
    assert len(problems) == 1 and problems[0].startswith("hosts: 403")
    hosts = {h.source_key for h in batches[0]}
    # Still known from policies and SQL Server, by the names those use.
    assert hosts == {"sql01.example.com", "app02", "sql01"}


# --- through the API -------------------------------------------------


def _register(blt_api: APIClient) -> NetBackupStore:
    """A primary exists once its first job run opens."""

    class NoJobs:
        def iter_pages(self, updated_after: datetime):  # noqa: ANN202
            return iter(())

    from blt.netbackup.collect import run_netbackup_collection

    store = NetBackupStore(blt_api, "nbu-lab")
    run_netbackup_collection(NoJobs(), store)
    return store


def _inventory(store: NetBackupStore, **fake: Any) -> Any:
    problems: list[str] = []
    with _fake_primary(**fake), NetBackupClient(URL, api_key="k") as nbu:
        return run_netbackup_inventory(
            lambda: iter_netbackup_inventory(nbu, problems), store, problems=problems
        )


def test_netbackup_assets_appear_beside_the_others(http: TestClient, blt_api: APIClient) -> None:
    store = _register(blt_api)
    watermark = http.get(f"{BASE}/last-run").json()["watermark"]
    run = _inventory(store)
    assert (run.mode, run.status, run.jobs_collected) == ("inventory", "succeeded", 11)
    # An inventory run is never the job watermark, nor the last job run.
    last = http.get(f"{BASE}/last-run").json()
    assert last["watermark"] == watermark and last["last_run"]["mode"] == "initial"

    objects = http.get(f"{BASE}/objects").json()
    assert len(objects) == 11
    by_name = {(o["kind"], o["name"]): o for o in objects}
    instance = by_name[("instance", "MSSQLSERVER")]
    assert by_name[("database", "sales")]["parent_id"] == instance["id"]
    assert instance["parent_id"] == by_name[("host", "sql01")]["id"]
    assert [o["name"] for o in http.get(f"{BASE}/objects", params={"kind": "vm"}).json()] == [
        "test03",
        "web01",
        "web02",
    ]

    rows = {
        (r["kind"], r["name"]): r
        for r in http.get("/v1/assets", params={"source_type": "netbackup"}).json()
    }
    assert {r["source_type"] for r in rows.values()} == {"netbackup"}
    assert {r["source_name"] for r in rows.values()} == {"nbu-lab"}
    assert rows[("database", "sales")]["verdict"] == "protected"
    assert rows[("database", "sales")]["location"] == "sql01 / MSSQLSERVER"
    # Protected by policy, but NetBackup gives no time and blt has no job yet.
    assert rows[("database", "hr")]["verdict"] == "stale"
    assert rows[("database", "crm")]["verdict"] == "unprotected"
    assert rows[("database", "tempdb")]["verdict"] == "excluded"
    assert rows[("instance", "MSSQLSERVER")]["verdict"] == "unknown"
    assert rows[("vm", "web01")]["verdict"] == "protected"
    assert rows[("vm", "web01")]["location"] == "vc01.example.com / dc1 / cl1"
    assert rows[("vm", "test03")]["verdict"] == "unprotected"
    assert rows[("host", "sql01")]["verdict"] == "stale"
    assert rows[("host", "app02")]["verdict"] == "unprotected"
    # Only in a deactivated policy: still a gap, but one that says which.
    assert rows[("host", "app02")]["deactivated_policies"] == ["fs-old"]
    assert rows[("host", "sql01")]["deactivated_policies"] is None
    assert rows[("vm", "test03")]["deactivated_policies"] is None

    summary = http.get("/v1/assets/summary").json()
    counts = {
        (c["kind"], c["verdict"]): c["count"]
        for c in summary["counts"]
        if c["source_type"] == "netbackup"
    }
    assert counts[("database", "unprotected")] == 1 and counts[("vm", "unprotected")] == 1
    assert "netbackup/nbu-lab" in summary["last_inventory"]


def _backup(job_id: int, client: str, policy: str, policy_type: str, ended: datetime, **kw: Any):
    return NetBackupJobIn(
        **{  # type: ignore[arg-type]
            "job_id": job_id,
            "job_type": "BACKUP",
            "state": "DONE",
            "status": 0,
            "is_active": False,
            "policy_name": policy,
            "policy_type": policy_type,
            "client_name": client,
            "start_time": ended - timedelta(minutes=20),
            "end_time": ended,
            "last_update_time": ended,
            **kw,
        }
    )


def test_a_host_s_last_backup_is_its_newest_successful_policy_job(
    http: TestClient, blt_api: APIClient
) -> None:
    store = _register(blt_api)
    _inventory(store)
    now = datetime.now(UTC)
    store.upsert_jobs(
        None,
        now,
        [
            # The host's file system policy, under the name the policy uses.
            _backup(1, "sql01.example.com", "fs-daily", "STANDARD", now - timedelta(hours=5)),
            # A failed later one does not count.
            _backup(2, "sql01", "fs-daily", "STANDARD", now - timedelta(hours=1), status=58),
            # Its SQL Server backups say nothing of its file systems.
            _backup(3, "sql01", "sql-prod", "MS_SQL_SERVER", now - timedelta(minutes=30)),
            # A VM backed up by a VIP policy NetBackup's record does not name.
            _backup(4, "test03", "vm-vip", "VMWARE", now - timedelta(hours=2)),
        ],
    )
    hosts = {o["name"]: o for o in http.get(f"{BASE}/objects", params={"kind": "host"}).json()}
    sql01 = hosts["sql01"]
    assert sql01["last_backup_from"] == "jobs" and sql01["last_backup_job_id"] == 1
    rows = {
        (r["kind"], r["name"]): r
        for r in http.get("/v1/assets", params={"source_type": "netbackup"}).json()
    }
    assert rows[("host", "sql01")]["verdict"] == "protected"
    # A last backup, but nothing NetBackup says protects it: still a gap.
    assert rows[("vm", "test03")]["last_backup_at"] is not None
    assert rows[("vm", "test03")]["verdict"] == "unprotected"
    # NetBackup's own time stays: web01's comes from the asset service.
    web01 = {o["name"]: o for o in http.get(f"{BASE}/objects", params={"kind": "vm"}).json()}
    assert web01["web01"]["last_backup_from"] == "netbackup"


def test_a_complete_run_marks_what_it_did_not_see_and_a_failed_one_nothing(
    http: TestClient, blt_api: APIClient
) -> None:
    store = _register(blt_api)
    _inventory(store)
    # test03 is gone from vCenter; the hosts listing is refused this time.
    failed = _inventory(store, vms=VMS[:2], hosts_status=403)
    assert failed.status == "failed" and "hosts" in (failed.error or "")
    names = {o["name"] for o in http.get(f"{BASE}/objects", params={"present": True}).json()}
    assert "test03" in names and "nbu01" in names

    done = _inventory(store, vms=VMS[:2])
    assert done.status == "succeeded"
    gone = http.get(f"{BASE}/objects", params={"present": False}).json()
    # test03, and the row the failed run made for sql01 under the policy's
    # name for it when it had no /config/hosts to map that name with.
    assert sorted(o["name"] for o in gone) == ["sql01.example.com", "test03"]
    assets_now = http.get("/v1/assets", params={"source_type": "netbackup"}).json()
    assert "test03" not in {r["name"] for r in assets_now}
    old = http.get("/v1/assets", params={"source_type": "netbackup", "present": False}).json()
    assert sorted(r["name"] for r in old) == ["sql01.example.com", "test03"]


def test_objects_need_their_parents_first_and_a_run(http: TestClient, blt_api: APIClient) -> None:
    store = _register(blt_api)
    run = http.post(
        f"{BASE}/runs",
        json={"mode": "inventory", "started_at": RECENT.isoformat(), "lookup_seconds": 0},
    ).json()
    orphan = {
        "kind": "database",
        "source_key": "db-x",
        "name": "x",
        "parent_kind": "instance",
        "parent_key": "nope",
    }
    body = {"run_id": run["id"], "collected_at": RECENT.isoformat(), "objects": [orphan]}
    response = http.post(f"{BASE}/objects", json=body)
    assert response.status_code == 422 and "send parents first" in response.text
    assert http.post(f"{BASE}/objects", json={**body, "run_id": 999999}).status_code == 404
    assert http.get("/v1/sources/netbackup/nobody/objects").status_code == 404
    assert store is not None
