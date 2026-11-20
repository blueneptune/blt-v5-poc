"""/v1/assets: the Commvault and Rubrik inventories side by side, each
row with its verdict."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sdk_primer import APIClient

from blt.api.assets import rubrik_kind, verdict
from blt.collector.collect import run_collection, run_inventory
from blt.collector.store import BltStore
from blt.schemas import JobIn, ObjectIn

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("protection", "age_days", "expected"),
    [
        ("protected", 1, "protected"),
        ("protected", 10, "stale"),
        ("protected", None, "stale"),
        ("unprotected", 1, "unprotected"),
        ("excluded", None, "excluded"),
        (None, 1, "unknown"),
    ],
)
def test_verdict(protection: str | None, age_days: int | None, expected: str) -> None:
    last = NOW - timedelta(days=age_days) if age_days is not None else None
    assert verdict(protection, last, NOW, 7) == expected


@pytest.mark.parametrize(
    ("object_type", "kind"),
    [
        ("VmwareVirtualMachine", "vm"),
        ("HypervVirtualMachine", "vm"),
        ("Ec2Instance", "vm"),
        ("AzureNativeVm", "vm"),
        ("Mssql", "database"),
        ("OracleDatabase", "database"),
        ("SapHanaDatabase", "database"),
        ("LinuxFileset", "file_system"),
        ("O365Mailbox", "m365"),
        ("PhysicalHost", "host"),
        ("SomethingNew", "other"),
    ],
)
def test_rubrik_kind(object_type: str, kind: str) -> None:
    assert rubrik_kind(object_type) == kind


class _NoJobs:
    def iter_pages(self, lookup_seconds: int, *, ended_between=None):  # noqa: ANN001, ANN202
        return iter(())

    def get(self, job_id: int) -> JobIn | None:
        return None

    def oldest_start(self, lookup_seconds: int) -> datetime | None:
        return None


def test_commvault_assets_with_their_verdicts(http: TestClient, blt_api: APIClient) -> None:
    store = BltStore(blt_api, "prod")
    run_collection(_NoJobs(), store)
    recent = datetime.now(UTC) - timedelta(hours=2)
    old = datetime.now(UTC) - timedelta(days=30)

    def batches():  # noqa: ANN202
        yield [
            ObjectIn(
                kind="client",
                source_key="15",
                name="sql01",
                protection="protected",
                last_backup_at=recent,
            ),
            ObjectIn(kind="client", source_key="20", name="vcenter"),
        ]
        yield [
            ObjectIn(
                kind="instance",
                source_key="15/81/6",
                name="MSSQLSERVER",
                parent_kind="client",
                parent_key="15",
                app_type="SQL Server",
            ),
        ]
        yield [
            ObjectIn(
                kind="database",
                source_key="15/81/6/sales",
                name="sales",
                parent_kind="instance",
                parent_key="15/81/6",
                app_type="SQL Server",
                plan="Gold",
                protection="protected",
                last_backup_at=old,
            ),
            ObjectIn(
                kind="database",
                source_key="15/81/6/hr",
                name="hr",
                parent_kind="instance",
                parent_key="15/81/6",
                app_type="SQL Server",
                protection="unprotected",
            ),
        ]
        yield [
            ObjectIn(
                kind="vm",
                source_key="4211-bb",
                name="test-vm",
                parent_kind="client",
                parent_key="20",
                app_type="VMW",
                protection="unprotected",
            ),
        ]

    run_inventory(batches, store)

    rows = {r["name"]: r for r in http.get("/v1/assets").json()}
    assert rows["sql01"]["kind"] == "host" and rows["sql01"]["verdict"] == "protected"
    assert rows["sales"]["verdict"] == "stale"
    assert rows["sales"]["location"] == "sql01 / MSSQLSERVER"
    assert rows["hr"]["verdict"] == "unprotected"
    assert rows["test-vm"]["kind"] == "vm" and rows["test-vm"]["location"] == "vcenter"
    assert rows["MSSQLSERVER"]["verdict"] == "unknown"
    # A wider window: 30 days old is no longer stale.
    wide = {r["name"]: r for r in http.get("/v1/assets", params={"stale_days": 60}).json()}
    assert wide["sales"]["verdict"] == "protected"

    gaps = http.get("/v1/assets", params={"verdict": "unprotected"}).json()
    assert sorted(r["name"] for r in gaps) == ["hr", "test-vm"]
    vms = http.get("/v1/assets", params={"kind": "vm", "source_type": "commvault"}).json()
    assert [r["name"] for r in vms] == ["test-vm"]

    # Paged, with the count of everything that matches.
    first = http.get("/v1/assets", params={"limit": 2})
    assert first.headers["X-Total-Count"] == "6" and len(first.json()) == 2
    beyond = http.get("/v1/assets", params={"limit": 2, "offset": 50, "kind": "database"})
    assert beyond.json() == [] and beyond.headers["X-Total-Count"] == "2"
    # A name's own % and _ are taken literally.
    assert http.get("/v1/assets", params={"name": "%"}).json() == []
    assert [r["name"] for r in http.get("/v1/assets", params={"name": "SAL"}).json()] == ["sales"]

    summary = http.get("/v1/assets/summary").json()
    counts = {(c["kind"], c["verdict"]): c["count"] for c in summary["counts"]}
    assert counts[("database", "unprotected")] == 1
    assert counts[("database", "stale")] == 1
    assert counts[("vm", "unprotected")] == 1
    assert "commvault/prod" in summary["last_inventory"]


def test_rubrik_assets_read_their_sla(http: TestClient) -> None:
    now = datetime.now(UTC)
    run = http.post(
        "/v1/sources/rubrik/acme/runs",
        json={"mode": "inventory", "started_at": now.isoformat(), "lookup_seconds": 0},
    )
    run_id = run.json()["id"]
    body = {
        "run_id": run_id,
        "collected_at": now.isoformat(),
        "objects": [
            {
                "source_key": "a",
                "rsc_id": "a",
                "name": "web01",
                "kind": "VmwareVirtualMachine",
                "protection_status": "Protected",
                "sla_domain_name": "Gold",
                "last_snapshot": now.isoformat(),
                "location": "vc01",
            },
            {
                "source_key": "b",
                "rsc_id": "b",
                "name": "sales",
                "kind": "Mssql",
                "protection_status": "NoSla",
            },
            {
                "source_key": "c",
                "rsc_id": "c",
                "name": "lab",
                "kind": "LinuxFileset",
                "protection_status": "DoNotProtect",
            },
        ],
    }
    assert http.post("/v1/sources/rubrik/acme/objects", json=body).status_code == 200

    rows = {r["name"]: r for r in http.get("/v1/assets", params={"source_type": "rubrik"}).json()}
    assert (rows["web01"]["kind"], rows["web01"]["verdict"], rows["web01"]["plan"]) == (
        "vm",
        "protected",
        "Gold",
    )
    assert (rows["sales"]["kind"], rows["sales"]["verdict"]) == ("database", "unprotected")
    assert rows["lab"]["verdict"] == "excluded"


def test_postgres_sorts_rubrik_types_as_python_does(http: TestClient) -> None:
    types = [
        "VmwareVirtualMachine",
        "HypervVirtualMachine",
        "Ec2Instance",
        "AzureNativeVm",
        "Mssql",
        "OracleDatabase",
        "SapHanaDatabase",
        "LinuxFileset",
        "NasShare",
        "O365Mailbox",
        "ExchangeDatabase",
        "PhysicalHost",
        "SomethingNew",
    ]
    now = datetime.now(UTC)
    run = http.post(
        "/v1/sources/rubrik/kinds/runs",
        json={"mode": "inventory", "started_at": now.isoformat(), "lookup_seconds": 0},
    ).json()
    objects = [{"source_key": t, "rsc_id": t, "name": t, "kind": t} for t in types]
    http.post(
        "/v1/sources/rubrik/kinds/objects",
        json={"run_id": run["id"], "collected_at": now.isoformat(), "objects": objects},
    )
    rows = http.get("/v1/assets", params={"source": "kinds", "verdict": "unknown"}).json()
    assert {r["name"]: r["kind"] for r in rows} == {t: rubrik_kind(t) for t in types}
