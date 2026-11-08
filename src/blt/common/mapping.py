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


# --- NetBackup ---------------------------------------------------------
# States and job types: the enums in NetBackup 10.2's API (admin.yaml,
# GET /admin/jobs). A job not DONE takes its state from `state`; a DONE
# job from its status code: 0 succeeded, 1 partly, 150 stopped by an
# administrator, others failed. Status texts: the codes seen most, worded
# as NetBackup's status code reference words them.

NETBACKUP_ACTIVE_STATES = _lower(
    {
        "queued": ("QUEUED",),
        "running": ("ACTIVE",),
        "waiting": ("REQUEUED", "SUSPENDED", "INCOMPLETE", "WAITING_FOR_RETRY"),
    }
)

NETBACKUP_KINDS = _lower(
    {
        "backup": ("BACKUP", "ARCHIVE", "DBBACKUP", "SNAPSHOT", "RMAN_CATALOG", "TRIAL_BACKUP"),
        "restore": (
            "RESTORE",
            "DBRECOVER",
            "INSTANT_RECOVERY",
            "ACTIVATE_IR",
            "DEACTIVATE_IR",
            "REACTIVATE_IR",
            "STOP_IR",
            "INSTANT_ACCESS",
        ),
        "copy": (
            "DUPLICATE",
            "REPLICATE",
            "IMPORT",
            "REPLICA_IMPORT",
            "SNAPDUPE",
            "SNAPREPLICATE",
            "SNAPIMPORT",
            "VAULT",
            "CLOUD_SNAPSHOT_REPLICATE",
        ),
        "maintenance": (
            "IMAGEDELETE",
            "INDEXCLEANUP",
            "LABEL",
            "ERASE",
            "TPCLEAN",
            "TPFORMAT",
            "TPREQ",
            "VMPHYSINV",
            "DQTS",
            "MCONTENTS",
            "DEPLOYMENT",
            "LOCKVM",
            "APP_STATE_CAPTURE",
            "PREPARING_FOR_BACKUP",
        ),
        "scan": ("VERIFY", "INDEXING", "SNAPINDEX"),
    }
)

NETBACKUP_STATUS_TEXT = {
    0: "the requested operation was successfully completed",
    1: "the requested operation was partially successful",
    2: "none of the requested files were backed up",
    13: "file read failed",
    23: "socket read failed",
    24: "socket write failed",
    25: "cannot connect on socket",
    41: "network connection timed out",
    42: "network read failed",
    48: "client hostname could not be found",
    50: "client process aborted",
    54: "timed out connecting to client",
    57: "client connection refused",
    58: "can't connect to client",
    59: "access to the client was not allowed",
    71: "none of the files in the file list exist",
    84: "media write error",
    96: "unable to allocate new media for backup, storage unit has none available",
    129: "disk storage unit is full",
    150: "termination requested by administrator",
    156: "snapshot error encountered",
    190: "found no images or media matching the selection criteria",
    191: "no images were successfully processed",
    196: "client backup was not attempted because backup window closed",
    213: "no storage units available for use",
    219: "the required storage unit is unavailable",
    800: "resource request failed",
    2074: "disk volume is down",
}


def netbackup_state(state: str | None, status: int | None) -> str:
    if (state or "").strip().upper() != "DONE":
        return _lookup(NETBACKUP_ACTIVE_STATES, state, "unknown")
    if status is None:
        return "unknown"
    if status == 0:
        return "succeeded"
    if status == 1:
        return "partial"
    if status == 150:
        return "cancelled"
    if status == 196:
        return "skipped"
    return "failed"


def netbackup_job(row: Mapping[str, Any]) -> dict[str, Any]:
    state = netbackup_state(row.get("state"), row.get("status"))
    done = (row.get("state") or "").upper() == "DONE"
    code = row.get("status")
    product_status = (row.get("state") or "") + (f" (status {code})" if done else "")
    message = None
    if done and code not in (None, 0):
        text = NETBACKUP_STATUS_TEXT.get(code)
        message = f"status {code}: {text}" if text else f"status {code}"
    kib = row.get("kilobytes_data_transferred")
    if kib is None:
        kib = row.get("kilobytes_transferred")
    return {
        "product_job_key": str(row["job_id"]),
        "kind": _lookup(NETBACKUP_KINDS, row.get("job_type"), "other"),
        "state": state,
        "product_status": product_status,
        "is_active": state in ACTIVE_STATES,
        "started_at": row.get("start_time"),
        "ended_at": row.get("end_time") if done else None,
        "updated_at": row.get("last_update_time"),
        "asset_name": row.get("client_name"),
        "asset_type": row.get("policy_type"),
        # The policy is what makes it run (an SLP may copy it on).
        "protection": row.get("policy_name"),
        "bytes_transferred": kib * 1024 if kib is not None else None,
        "bytes_protected": None,
        "message": message,
        "attempt": row.get("try_number"),
        "last_collected_at": row["last_collected_at"],
    }
