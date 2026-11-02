"""The wire contract for Rubrik, between its collector and the API.

Same idea as blt.schemas for Commvault: both halves import these, so
what the collector sends is checked by the model the API reads it with.
Nothing here needs the Rubrik SDK or Postgres.

A Rubrik "job" is an RSC activity series: one backup, recovery,
replication or other run. It is keyed by `series_key` (RSC's global id
for it, see rubrik_sdk.ActivitySeries.key) within one RSC account.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# initial   - first ever run: whatever changed in a recent window
# delta     - whatever changed since the watermark (less an overlap)
# full      - forced: whatever changed in the whole history limit
# inventory - not jobs at all: every object RSC knows about
RubrikRunMode = Literal["initial", "delta", "full", "inventory"]
# The modes that collect jobs, and so move the watermark.
JOB_MODES = ("initial", "delta", "full")
RubrikRunStatus = Literal["running", "succeeded", "failed"]


class RubrikJobIn(BaseModel):
    """One Rubrik job (an RSC activity series) as of one collection."""

    series_key: str = Field(
        description=(
            "blt's key for the job: RSC's fid, or cluster id and series id when there is none."
        )
    )
    fid: str | None = Field(
        default=None, description="RSC's global id for the series, where it has one."
    )
    activity_series_id: str | None = Field(
        default=None, description="The series' id on its cluster."
    )
    cluster_id: str | None = Field(
        default=None, description="The Rubrik cluster that ran it (a UUID)."
    )
    cluster_name: str | None = Field(default=None, description="That cluster's name.")
    object_id: str | None = Field(default=None, description="RSC's id for the protected object.")
    object_name: str | None = Field(
        default=None, description="The protected object: a VM, host, database, mailbox, site, ..."
    )
    object_type: str | None = Field(
        default=None,
        description=("Its type, in RSC's words: VmwareVm, LinuxHost, O365Mailbox, O365Site, ..."),
    )
    location: str | None = Field(
        default=None,
        description="Where the object is, in RSC's words (its host, vCenter, tenant, ...).",
    )
    activity_type: str | None = Field(
        default=None,
        description=(
            "What sort of work: Backup, Recovery, Replication, Archive, Index, "
            "Diagnostic, Classification, THREAT_MONITORING, ..."
        ),
    )
    status: str | None = Field(
        default=None,
        description="RSC's status for the job's latest activity: Success, Failure, Running, ...",
    )
    is_active: bool = Field(default=False, description="Still running or queued.")
    severity: str | None = Field(
        default=None, description="RSC's severity for the latest activity: Info, Warning, ..."
    )
    progress: str | None = Field(default=None, description='Progress as RSC words it, e.g. "45%".')
    percent_complete: float | None = Field(default=None, description="`progress` as a number.")
    sla_domain_name: str | None = Field(
        default=None, description="The SLA domain protecting the object, where RSC gives it."
    )
    last_activity_message: str | None = Field(
        default=None, description="The latest event's message, as RSC words it."
    )
    failure_reason: str | None = Field(default=None, description="Why the job failed, if it did.")
    cause_error_code: str | None = Field(
        default=None, description="RSC's error code for the failure."
    )
    cause_error_message: str | None = Field(default=None, description="The error, in RSC's words.")
    cause_error_reason: str | None = Field(
        default=None, description="Why it happened, in RSC's words."
    )
    cause_error_remedy: str | None = Field(
        default=None, description="What RSC suggests doing about it."
    )
    attempt_number: int | None = Field(
        default=None, description="Which attempt this is: above 1 for a retry."
    )
    is_on_demand: bool | None = Field(
        default=None, description="Run by hand rather than by its SLA domain."
    )
    data_transferred: int | None = Field(default=None, description="Bytes.")
    logical_size: int | None = Field(default=None, description="Bytes.")
    effective_throughput: int | None = Field(default=None, description="Bytes per second.")
    start_time: AwareDatetime | None = Field(default=None, description="When the job started.")
    last_updated: AwareDatetime | None = Field(
        default=None,
        description="When RSC last changed the job. RSC has no end time: for a finished job, "
        "this is when it ended.",
    )
    raw: dict[str, Any] = Field(
        default_factory=dict, description="RSC's activity series record, untouched."
    )


class RubrikJobBatch(BaseModel):
    """Jobs collected in one go, sent during a run."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "run_id": 88,
                    "collected_at": "2026-10-29T14:01:00Z",
                    "jobs": [
                        {
                            "series_key": "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b",
                            "object_name": "vm-app-01",
                            "object_type": "VmwareVm",
                            "activity_type": "Backup",
                            "status": "Success",
                            "is_active": False,
                            "sla_domain_name": "Gold",
                            "data_transferred": 1073741824,
                            "logical_size": 42949672960,
                            "start_time": "2026-10-29T13:00:00Z",
                            "last_updated": "2026-10-29T13:20:00Z",
                        }
                    ],
                }
            ]
        }
    )

    run_id: int | None = Field(
        default=None, description="The run this batch belongs to: the `id` from `POST .../runs`."
    )
    collected_at: AwareDatetime = Field(
        description=(
            "When this was read from the source. Nothing older than what is already stored "
            "overwrites it."
        )
    )
    jobs: list[RubrikJobIn] = Field(description="The jobs, each as it was at collected_at.")


class RubrikJobOut(RubrikJobIn):
    """A Rubrik job as blt holds it: the latest state it was seen in, plus blt's own fields."""

    model_config = ConfigDict(from_attributes=True)

    account_id: int = Field(description="blt's id for the RSC account.")
    first_seen_at: AwareDatetime = Field(description="When blt first collected the job.")
    last_collected_at: AwareDatetime = Field(
        description="When this state of the job was read from RSC."
    )
    last_seen_run_id: int | None = Field(
        default=None, description="The last run that collected the job."
    )


class RubrikRunStart(BaseModel):
    """Opens a run."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"mode": "delta", "started_at": "2026-10-29T14:00:00Z", "lookup_seconds": 3900}
            ]
        }
    )

    mode: RubrikRunMode = Field(
        description=(
            "initial: first ever run, a recent window. delta: changes since the watermark, less an"
            " overlap. full: changes within the whole history limit. inventory: every object RSC "
            "knows about, not jobs."
        )
    )
    started_at: AwareDatetime = Field(
        description="When the run started. A succeeded run's start becomes the watermark."
    )
    lookup_seconds: int = Field(
        description=(
            "How far back the run asked for changes: everything updated after started_at minus "
            "this."
        )
    )


class RubrikRunFinish(BaseModel):
    """How a run ended."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "succeeded",
                    "finished_at": "2026-10-29T14:01:40Z",
                    "jobs_collected": 180,
                }
            ]
        }
    )

    status: Literal["succeeded", "failed"] = Field(
        description="succeeded moves the watermark; failed does not."
    )
    finished_at: AwareDatetime = Field(description="When the run finished.")
    jobs_collected: int = Field(
        default=0, description="Jobs stored by the run; objects, for an inventory run."
    )
    error: str | None = Field(default=None, description="Why a failed run failed.")


class RubrikRunOut(BaseModel):
    """One Rubrik collection run."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="The run's id: send it with each batch, and to finish the run.")
    account_id: int = Field(description="blt's id for the RSC account.")
    mode: RubrikRunMode = Field(description="What kind of run it is (see RubrikRunStart).")
    status: RubrikRunStatus = Field(description="running until finished; then succeeded or failed.")
    started_at: AwareDatetime = Field(description="When the collector started the run.")
    finished_at: AwareDatetime | None = Field(
        default=None, description="When it finished. Null while running."
    )
    lookup_seconds: int = Field(description="How far back the run asked for changes.")
    jobs_collected: int = Field(
        description="Jobs stored from this run; objects, for an inventory run."
    )
    error: str | None = Field(default=None, description="Why a failed run failed.")


class RubrikLastRun(BaseModel):
    """`watermark` is when the most recent successful job run started -
    what a delta run reaches back to. None: never collected, do an
    initial run. Inventory runs play no part in it."""

    account: str = Field(description="blt's name for the RSC account.")
    watermark: AwareDatetime | None = Field(
        default=None, description="When the last successful run started. Null: never collected."
    )
    last_run: RubrikRunOut | None = Field(
        default=None, description="The most recent job run, whatever its outcome."
    )


class RubrikObjectIn(BaseModel):
    """One object RSC knows about - a VM, host, database, file set,
    mailbox, OneDrive, site, team, ... - as of one inventory."""

    source_key: str = Field(description="blt's key for the object: RSC's fid, else its id.")
    fid: str | None = Field(default=None, description="RSC's global id for the object.")
    rsc_id: str = Field(description="RSC's id for the object.")
    name: str = Field(description="The object's name.")
    kind: str | None = Field(
        default=None,
        description="Its type, in RSC's words: AzureNativeVm, O365Mailbox, NAS_FILESET, ...",
    )
    location: str | None = Field(
        default=None, description="Where it is, in RSC's words (its host, vCenter, tenant, ...)."
    )
    object_state: str | None = Field(
        default=None,
        description="ACTIVE; RELIC once gone from its source but still holding snapshots.",
    )
    protection_status: str | None = Field(
        default=None, description="Protected, NoSla or DoNotProtect."
    )
    compliance_status: str | None = Field(
        default=None,
        description="Whether its backups meet its SLA domain: IN_COMPLIANCE, OUT_OF_COMPLIANCE, "
        "UNPROTECTED, NOT_AVAILABLE, ...",
    )
    archival_compliance_status: str | None = Field(
        default=None, description="The same, for its archive copies."
    )
    replication_compliance_status: str | None = Field(
        default=None, description="The same, for its replicas."
    )
    sla_domain_id: str | None = Field(default=None, description="RSC's id for its SLA domain.")
    sla_domain_name: str | None = Field(
        default=None, description="The SLA domain protecting it, if any."
    )
    cluster_id: str | None = Field(
        default=None, description="The Rubrik cluster that protects it (a UUID)."
    )
    cluster_name: str | None = Field(default=None, description="That cluster's name.")
    org_id: str | None = Field(
        default=None, description="The RSC organisation it belongs to, on a multi-tenant account."
    )
    protected_on: AwareDatetime | None = Field(
        default=None, description="When it was first protected."
    )
    last_snapshot: AwareDatetime | None = Field(
        default=None, description="Its latest snapshot (backup)."
    )
    latest_archival_snapshot: AwareDatetime | None = Field(
        default=None, description="Its latest archived snapshot."
    )
    latest_replication_snapshot: AwareDatetime | None = Field(
        default=None, description="Its latest replicated snapshot."
    )
    awaiting_first_full: bool | None = Field(
        default=None, description="Protected, but its first full backup has not happened yet."
    )
    total_snapshots: int | None = Field(default=None, description="Snapshots it has.")
    missed_snapshots: int | None = Field(
        default=None, description="Snapshots its SLA domain called for and did not get."
    )
    local_snapshots: int | None = Field(default=None, description="Snapshots on the cluster.")
    archive_snapshots: int | None = Field(default=None, description="Snapshots in archive.")
    replica_snapshots: int | None = Field(default=None, description="Snapshots replicated.")
    logical_bytes: int | None = Field(default=None, description="Its size, as the source sees it.")
    physical_bytes: int | None = Field(default=None, description="Bytes its snapshots take.")
    local_storage: int | None = Field(default=None, description="Bytes on the cluster.")
    archive_storage: int | None = Field(default=None, description="Bytes in archive.")
    replica_storage: int | None = Field(default=None, description="Bytes replicated.")
    transferred_bytes: int | None = Field(default=None, description="Bytes transferred.")
    pull_time: AwareDatetime | None = Field(
        default=None, description="When RSC last refreshed this object's figures."
    )


class RubrikObjectBatch(BaseModel):
    """Objects sent during an inventory run."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "run_id": 91,
                    "collected_at": "2026-10-30T05:47:00Z",
                    "objects": [
                        {
                            "source_key": "6a1c2d3e-4f50-4617-8829-3a4b5c6d7e8f",
                            "rsc_id": "6a1c2d3e-4f50-4617-8829-3a4b5c6d7e8f",
                            "name": "vm-app-01",
                            "kind": "AzureNativeVm",
                            "object_state": "ACTIVE",
                            "protection_status": "Protected",
                            "compliance_status": "IN_COMPLIANCE",
                            "sla_domain_name": "Gold",
                            "last_snapshot": "2026-10-30T02:10:00Z",
                            "total_snapshots": 42,
                        }
                    ],
                }
            ]
        }
    )

    run_id: int = Field(description="The inventory run this batch belongs to.")
    collected_at: AwareDatetime = Field(description="When this was read from RSC.")
    objects: list[RubrikObjectIn] = Field(description="The objects.")


class RubrikObjectOut(RubrikObjectIn):
    """An object as blt holds it: as of the latest inventory that listed
    it, plus blt's own fields."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="blt's id for the object.")
    account_id: int = Field(description="blt's id for the RSC account.")
    present: bool = Field(
        description="Listed by the latest inventory. False: it was known, and RSC no longer "
        "lists it."
    )
    first_seen_at: AwareDatetime = Field(description="When an inventory first listed it.")
    last_seen_at: AwareDatetime = Field(description="When an inventory last listed it.")
    last_seen_run_id: int | None = Field(
        default=None, description="The last inventory run that listed it."
    )
