"""common.job: each product's mapping, row by row (docs/common-jobs.md),
and the projection - written with every product upsert, never rolled
back by an older batch, linked to inventory, and rebuilt from the
product tables."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from blt.common.mapping import (
    ACTIVE_STATES,
    COMMVAULT_STATES,
    KINDS,
    RUBRIK_KINDS,
    STATES,
    commvault_job,
    rubrik_job,
)

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

# RSC's ActivityStatusEnum and ActivityTypeEnum, complete, as its GraphQL
# schema listed them on 2026-10-28 (yoki-py-rubrik-sdk masked samples).
RUBRIK_STATUSES = [
    "Canceled", "Canceling", "Failure", "Info", "PARTIAL_SUCCESS", "Queued",
    "Running", "Success", "TaskFailure", "TaskSuccess", "Warning",
]  # fmt: skip
RUBRIK_ACTIVITY_TYPES = [
    "Anomaly", "Archive", "AuthDomain", "AwsEvent", "BULK_RECOVERY", "Backup",
    "CLOUD_DIRECT_ARCHIVE", "COPY", "Classification", "CloudNativeSource",
    "CloudNativeVirtualMachine", "CloudNativeVm", "Configuration", "Connection",
    "Conversion", "DISCOVER", "Diagnostic", "Discovery", "Download",
    "ENCRYPTION_MANAGEMENT_OPERATION", "EmbeddedEvent", "Failover", "Fileset",
    "Hardware", "Hdfs", "HostEvent", "HypervScvmm", "HypervServer",
    "IDENTITY_ACTIVITY", "IDENTITY_ALERTS", "IDENTITY_VIOLATION",
    "ISOLATED_RECOVERY", "Index", "Instantiate", "LOG_BACKUP", "LegalHold",
    "LocalRecovery", "LockSnapshot", "Maintenance", "NutanixCluster", "OWNERSHIP",
    "PERMISSION_ASSESSMENT", "PROTECTED_OBJECT_DELETION", "QUARANTINE",
    "RadarAnalysis", "Recovery", "Replication", "ResourceOperations",
    "SCHEDULE_RECOVERY", "SECURITY_VIOLATION", "SEEDING", "Storage",
    "StorageArray", "StormResource", "Support", "Sync", "System", "TENANT_OVERLAP",
    "TENANT_QUOTA", "THREAT_FEED", "THREAT_MONITORING", "TestFailover",
    "ThreatHunt", "Tpr", "USER_INTELLIGENCE", "UnknownEventType", "Upgrade",
    "VCenter", "Vcd", "VolumeGroup", "AGENT_CLOUD_SECURITY_ALERT", "REENCRYPTION",
    "PRE_SEEDING", "AGENT_CLOUD_REWIND",
]  # fmt: skip


def cv(status: str, **fields: Any) -> dict[str, Any]:
    return {"job_id": 7, "status": status, "last_collected_at": T0, **fields}


def rk(status: str | None, **fields: Any) -> dict[str, Any]:
    return {"series_key": "s1", "status": status, "last_collected_at": T0, **fields}


# --- the mappings, one row of the design's tables each ------------------


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("Queued", "queued"),
        ("Running", "running"),
        ("Kill Pending", "running"),
        ("Suspend Pending", "running"),
        ("Interrupt Pending", "running"),
        ("Waiting", "waiting"),
        ("Pending", "waiting"),
        ("Suspended", "waiting"),
        ("Completed", "succeeded"),
        ("Committed", "succeeded"),
        ("Completed w/ one or more warnings", "warning"),
        ("Completed w/ one or more errors", "partial"),
        ("Failed", "failed"),
        ("Failed to Start", "failed"),
        ("Killed", "cancelled"),
        ("No Run", "skipped"),
        ("Not found in CommCell", "missing"),
        ("Something New", "unknown"),
    ],
)
def test_commvault_states(status: str, state: str) -> None:
    job = commvault_job(cv(status))
    assert (job["state"], job["product_status"]) == (state, status)
    assert job["is_active"] is (state in ACTIVE_STATES)


@pytest.mark.parametrize(
    ("operation", "job_type", "kind"),
    [
        # Every type collected from cv-toaster up to 2026-11-02.
        ("Backup", "Backup", "backup"),
        ("Restore", None, "restore"),
        ("Aux Copy", None, "copy"),
        ("Data Aging", "Prune", "maintenance"),
        ("DDB Verification", "DDB Verification", "maintenance"),
        ("Disaster Recovery Backup", "CS DR Backup", "maintenance"),
        ("Index Server backup", "Index Server Backup", "maintenance"),
        ("Load Balance DeDuplication DB Partitions", None, "maintenance"),
        ("Load Balance Index Servers", None, "maintenance"),
        ("DBMaintenance", None, "maintenance"),
        ("Software Sync", None, "maintenance"),
        ("Install Software", "Install Client", "maintenance"),
        ("Download Software", "Patch Download", "maintenance"),
        ("UpdateApprovalUserGroupAuthorization", None, "other"),
        (None, "Backup", "backup"),
        (None, None, "other"),
    ],
)
def test_commvault_kinds(operation: str | None, job_type: str | None, kind: str) -> None:
    assert commvault_job(cv("Completed", operation=operation, job_type=job_type))["kind"] == kind


def test_commvault_columns() -> None:
    job = commvault_job(
        cv(
            "Completed w/ one or more errors",
            start_time=T0 - timedelta(hours=1),
            end_time=T0,
            last_update_time=T0,
            client_name="web01",
            app_type="Linux File System",
            storage_policy="Gold",
            size_of_media=10,
            size_of_application=100,
            pending_reason="2 files failed",
        )
    )
    assert job == {
        "product_job_key": "7",
        "kind": "other",
        "state": "partial",
        "product_status": "Completed w/ one or more errors",
        "is_active": False,
        "started_at": T0 - timedelta(hours=1),
        "ended_at": T0,
        "updated_at": T0,
        "asset_name": "web01",
        "asset_type": "Linux File System",
        "protection": "Gold",
        "bytes_transferred": 10,
        "bytes_protected": 100,
        "message": "2 files failed",
        "attempt": None,
        "last_collected_at": T0,
    }


def test_the_commvault_statuses_blt_knew_all_map() -> None:
    from blt.schemas import _STATES

    assert set(_STATES) == set(COMMVAULT_STATES)


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("Queued", "queued"),
        ("Running", "running"),
        ("Canceling", "running"),
        ("Success", "succeeded"),
        ("TaskSuccess", "succeeded"),
        ("Warning", "warning"),
        ("Info", "info"),
        ("PARTIAL_SUCCESS", "partial"),
        ("Failure", "failed"),
        ("TaskFailure", "failed"),
        ("Canceled", "cancelled"),
        ("SomethingNew", "unknown"),
        (None, "unknown"),
    ],
)
def test_rubrik_states(status: str | None, state: str) -> None:
    assert rubrik_job(rk(status))["state"] == state


def test_every_rubrik_status_is_mapped() -> None:
    assert {rubrik_job(rk(s))["state"] for s in RUBRIK_STATUSES} <= set(STATES) - {"unknown"}


def test_every_rubrik_activity_type_has_a_kind() -> None:
    unlisted = {t.lower() for t in RUBRIK_ACTIVITY_TYPES}
    # The mapping names nothing RSC does not have.
    assert set(RUBRIK_KINDS) <= unlisted
    for activity in RUBRIK_ACTIVITY_TYPES:
        assert rubrik_job(rk("Success", activity_type=activity))["kind"] in KINDS


@pytest.mark.parametrize(
    ("activity", "kind"),
    [
        ("Backup", "backup"),
        ("LOG_BACKUP", "backup"),
        ("Recovery", "restore"),
        ("Download", "restore"),
        ("Replication", "copy"),
        ("Archive", "copy"),
        ("Index", "maintenance"),
        ("Diagnostic", "maintenance"),
        ("Configuration", "maintenance"),
        ("Classification", "scan"),
        ("RadarAnalysis", "scan"),
        ("THREAT_MONITORING", "scan"),
        ("HostEvent", "other"),
    ],
)
def test_rubrik_kinds(activity: str, kind: str) -> None:
    assert rubrik_job(rk("Success", activity_type=activity))["kind"] == kind


def test_rubrik_columns() -> None:
    finished = rubrik_job(
        rk(
            "Failure",
            activity_type="Backup",
            start_time=T0 - timedelta(hours=1),
            last_updated=T0,
            object_name="vm-01",
            object_type="VmwareVm",
            sla_domain_name="Gold",
            data_transferred=10,
            logical_size=100,
            failure_reason="snapshot failed",
            last_activity_message="Failed to back up vm-01",
            attempt_number=2,
        )
    )
    assert finished["ended_at"] == T0 and finished["message"] == "snapshot failed"
    assert (finished["asset_name"], finished["protection"], finished["attempt"]) == (
        "vm-01",
        "Gold",
        2,
    )
    running = rubrik_job(rk("Running", last_updated=T0, last_activity_message="50% done"))
    assert running["is_active"] and running["ended_at"] is None
    assert running["message"] == "50% done"


def test_the_design_document_lists_the_same_states_and_kinds() -> None:
    doc = (Path(__file__).resolve().parent.parent / "docs" / "common-jobs.md").read_text()
    for name in (*STATES, *KINDS):
        assert f"| `{name}` |" in doc, name


# --- the projection, against Postgres -------------------------------------


def common_jobs(database_url: str) -> dict[str, dict[str, Any]]:
    engine = create_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT s.type, s.name, j.* FROM common.job j "
                "JOIN common.source s ON s.id = j.source_id"
            )
        ).mappings()
        result = {f"{r['type']}/{r['name']}/{r['product_job_key']}": dict(r) for r in rows}
    engine.dispose()
    return result


def post_cv(http: TestClient, status: str, at: datetime, job_id: int = 1) -> None:
    body = {
        "collected_at": at.isoformat(),
        "jobs": [
            {"job_id": job_id, "status": status, "operation": "Backup", "client_name": "web01"}
        ],
    }
    assert http.post("/v1/sources/commvault/prod/jobs", json=body).status_code == 200


def post_rk(http: TestClient, status: str, at: datetime, **fields: Any) -> None:
    job = {"series_key": "s1", "status": status, "activity_type": "Backup", **fields}
    body = {"collected_at": at.isoformat(), "jobs": [job]}
    assert http.post("/v1/sources/rubrik/acme/jobs", json=body).status_code == 200


def test_each_upsert_writes_common_job(http: TestClient, database_url: str) -> None:
    post_cv(http, "Running", T0)
    post_rk(http, "Queued", T0, object_name="vm-01")

    jobs = common_jobs(database_url)
    assert set(jobs) == {"commvault/prod/1", "rubrik/acme/s1"}
    assert (jobs["commvault/prod/1"]["state"], jobs["commvault/prod/1"]["kind"]) == (
        "running",
        "backup",
    )
    assert jobs["rubrik/acme/s1"]["state"] == "queued"
    assert jobs["rubrik/acme/s1"]["asset_name"] == "vm-01"

    first = jobs["commvault/prod/1"]
    post_cv(http, "Completed", T0 + timedelta(minutes=5))
    now = common_jobs(database_url)["commvault/prod/1"]
    # The same blt job id, moved on.
    assert now["id"] == first["id"] and now["state"] == "succeeded" and not now["is_active"]
    assert now["first_seen_at"] == first["first_seen_at"]


def test_an_older_batch_does_not_roll_common_job_back(http: TestClient, database_url: str) -> None:
    post_cv(http, "Completed", T0 + timedelta(minutes=5))
    post_cv(http, "Running", T0)
    assert common_jobs(database_url)["commvault/prod/1"]["state"] == "succeeded"


def test_a_job_marked_missing_is_missing_in_common_job(http: TestClient, database_url: str) -> None:
    post_cv(http, "Running", T0)
    patch = {
        "status": "Not found in CommCell",
        "collected_at": (T0 + timedelta(hours=1)).isoformat(),
    }
    assert http.patch("/v1/sources/commvault/prod/jobs/1", json=patch).status_code == 200
    job = common_jobs(database_url)["commvault/prod/1"]
    assert (job["state"], job["is_active"]) == ("missing", False)


def test_jobs_are_linked_to_the_inventory(http: TestClient, database_url: str) -> None:
    # A job collected before the object was inventoried ...
    post_rk(http, "Success", T0, object_id="obj-1")
    assert common_jobs(database_url)["rubrik/acme/s1"]["source_object_id"] is None

    # ... is linked when an inventory run that lists it succeeds,
    run = http.post(
        "/v1/sources/rubrik/acme/runs",
        json={"mode": "inventory", "started_at": T0.isoformat(), "lookup_seconds": 0},
    ).json()
    objects = {
        "run_id": run["id"],
        "collected_at": T0.isoformat(),
        "objects": [{"source_key": "obj-1", "rsc_id": "obj-1", "name": "vm-01"}],
    }
    assert http.post("/v1/sources/rubrik/acme/objects", json=objects).status_code == 200
    finish = {"status": "succeeded", "finished_at": T0.isoformat(), "jobs_collected": 0}
    http.post(f"/v1/sources/rubrik/acme/runs/{run['id']}/finish", json=finish)
    object_id = http.get("/v1/sources/rubrik/acme/objects").json()[0]["id"]
    assert str(common_jobs(database_url)["rubrik/acme/s1"]["source_object_id"]) == object_id

    # ... and a job collected after it is linked as it is stored.
    post_rk(http, "Success", T0, series_key="s2", object_id="obj-1")
    assert str(common_jobs(database_url)["rubrik/acme/s2"]["source_object_id"]) == object_id


def test_reproject_rebuilds_common_job_from_the_product_tables(
    http: TestClient, database_url: str
) -> None:
    from blt.api.reproject import needs_first_projection, reproject

    for job_id in range(1, 6):
        post_cv(http, "Completed", T0, job_id=job_id)
    post_rk(http, "Success", T0)
    before = common_jobs(database_url)

    engine = create_engine(database_url)
    try:
        assert not needs_first_projection(engine)
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM common.job"))
        assert needs_first_projection(engine)

        assert reproject(engine, log=lambda _: None) == 6
        assert not needs_first_projection(engine)
    finally:
        engine.dispose()

    after = common_jobs(database_url)
    strip = lambda jobs: {k: {c: v for c, v in j.items() if c != "id"} for k, j in jobs.items()}  # noqa: E731
    assert json.dumps(strip(after), default=str, sort_keys=True) == json.dumps(
        strip(before), default=str, sort_keys=True
    )
