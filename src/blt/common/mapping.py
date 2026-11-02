"""Each product's job, in blt's common shape (docs/common-jobs.md).

One function per product, from the product's stored row (as a dict: the
row being upserted, or a product table row's model_dump) to the columns
of `common.job` that come from it. Pure - no database - so every mapping
row in the design document is a test (tests/test_common_jobs.py).

Lists are matched case-insensitively. A status a product reports that
is not listed here becomes `unknown`, which counts as active: wrongly
calling a live job finished means it is never refreshed, where the
other mistake costs one extra lookup. A type not listed becomes kind
`other`. `SELECT product_status, count(*) FROM common.job WHERE state =
'unknown' GROUP BY 1` finds the statuses still to map.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

STATES = (
    "queued",
    "running",
    "waiting",
    "succeeded",
    "warning",
    "info",
    "partial",
    "failed",
    "cancelled",
    "skipped",
    "missing",
    "unknown",
)
ACTIVE_STATES = frozenset({"queued", "running", "waiting", "unknown"})
KINDS = ("backup", "restore", "copy", "maintenance", "scan", "other")


def _lookup(table: Mapping[str, str], value: str | None, default: str) -> str:
    if value is None:
        return default
    return table.get(value.strip().lower(), default)


def _lower(table: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """{target: (product values, ...)} -> {product value lower-cased: target}"""
    return {value.lower(): target for target, values in table.items() for value in values}


# --- Commvault ---------------------------------------------------------
# Statuses: those blt.schemas has known since 2026-10-05 (plus blt's own
# "Not found in CommCell"); Commvault's full list is step 2a. Types:
# `operation`, Commvault's name for the work (`job_type` where it is
# missing) - the ones seen on cv-toaster, plus Restore and Aux Copy.

COMMVAULT_STATES = _lower(
    {
        "queued": ("Queued",),
        "running": ("Running", "Kill Pending", "Suspend Pending", "Interrupt Pending"),
        "waiting": ("Waiting", "Pending", "Suspended"),
        "succeeded": ("Completed", "Committed"),
        "warning": ("Completed w/ one or more warnings",),
        "partial": ("Completed w/ one or more errors",),
        "failed": ("Failed", "Failed to Start"),
        "cancelled": ("Killed",),
        "skipped": ("No Run",),
        "missing": ("Not found in CommCell",),
    }
)

COMMVAULT_KINDS = _lower(
    {
        "backup": ("Backup",),
        "restore": ("Restore",),
        "copy": ("Aux Copy", "Auxiliary Copy"),
        "maintenance": (
            "Data Aging",
            "Prune",
            "DDB Verification",
            "DBMaintenance",
            "Disaster Recovery Backup",
            "CS DR Backup",
            "Index Server backup",
            "Load Balance DeDuplication DB Partitions",
            "Load Balance Index Servers",
            "Install Software",
            "Download Software",
            "Software Sync",
        ),
    }
)


def commvault_job(row: Mapping[str, Any]) -> dict[str, Any]:
    state = _lookup(COMMVAULT_STATES, row["status"], "unknown")
    return {
        "product_job_key": str(row["job_id"]),
        "kind": _lookup(COMMVAULT_KINDS, row.get("operation") or row.get("job_type"), "other"),
        "state": state,
        "product_status": row["status"],
        "is_active": state in ACTIVE_STATES,
        "started_at": row.get("start_time"),
        "ended_at": row.get("end_time"),
        "updated_at": row.get("last_update_time"),
        "asset_name": row.get("client_name"),
        "asset_type": row.get("app_type"),
        # The plan (a storage policy underneath) is what makes it run.
        "protection": row.get("storage_policy"),
        "bytes_transferred": row.get("size_of_media"),
        "bytes_protected": row.get("size_of_application"),
        "message": row.get("pending_reason"),
        "attempt": None,
        "last_collected_at": row["last_collected_at"],
    }


# --- Rubrik ------------------------------------------------------------
# Statuses: RSC's ActivityStatusEnum, all eleven, read from its GraphQL
# schema on 2026-10-28 (yoki-py-rubrik-sdk samples). Types: its
# ActivityTypeEnum (74 values); the ones that are not work on a protected
# object (events, alerts, hardware) stay `other`.

RUBRIK_STATES = _lower(
    {
        "queued": ("Queued",),
        "running": ("Running", "Canceling"),
        "succeeded": ("Success", "TaskSuccess"),
        "warning": ("Warning",),
        "info": ("Info",),
        "partial": ("PARTIAL_SUCCESS",),
        "failed": ("Failure", "TaskFailure"),
        "cancelled": ("Canceled",),
    }
)

RUBRIK_KINDS = _lower(
    {
        "backup": ("Backup", "LOG_BACKUP"),
        "restore": (
            "Recovery",
            "LocalRecovery",
            "BULK_RECOVERY",
            "ISOLATED_RECOVERY",
            "SCHEDULE_RECOVERY",
            "Download",
            "Instantiate",
            "Failover",
            "TestFailover",
        ),
        "copy": (
            "Replication",
            "Archive",
            "CLOUD_DIRECT_ARCHIVE",
            "COPY",
            "SEEDING",
            "PRE_SEEDING",
        ),
        "maintenance": (
            "Index",
            "Diagnostic",
            "Configuration",
            "Maintenance",
            "Upgrade",
            "Discovery",
            "DISCOVER",
            "LockSnapshot",
            "LegalHold",
            "PROTECTED_OBJECT_DELETION",
            "ENCRYPTION_MANAGEMENT_OPERATION",
            "REENCRYPTION",
        ),
        "scan": (
            "Classification",
            "RadarAnalysis",
            "THREAT_MONITORING",
            "ThreatHunt",
            "Anomaly",
            "QUARANTINE",
        ),
    }
)


def rubrik_job(row: Mapping[str, Any]) -> dict[str, Any]:
    state = _lookup(RUBRIK_STATES, row.get("status"), "unknown")
    active = state in ACTIVE_STATES
    return {
        "product_job_key": row["series_key"],
        "kind": _lookup(RUBRIK_KINDS, row.get("activity_type"), "other"),
        "state": state,
        "product_status": row.get("status") or "",
        "is_active": active,
        "started_at": row.get("start_time"),
        # RSC gives no end time; a finished series' last update is when
        # it finished.
        "ended_at": None if active else row.get("last_updated"),
        "updated_at": row.get("last_updated"),
        "asset_name": row.get("object_name"),
        "asset_type": row.get("object_type"),
        "protection": row.get("sla_domain_name"),
        "bytes_transferred": row.get("data_transferred"),
        "bytes_protected": row.get("logical_size"),
        "message": row.get("failure_reason") or row.get("last_activity_message"),
        "attempt": row.get("attempt_number"),
        "last_collected_at": row["last_collected_at"],
    }
