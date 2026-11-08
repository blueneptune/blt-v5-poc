"""The wire contract for NetBackup jobs, between its collector and the API.

As blt.rubrik.schemas is for Rubrik: both halves import these, so what
the collector sends is checked by the model the API reads it with.
Nothing here needs the NetBackup SDK or Postgres.

A NetBackup job is one entry in the primary's activity monitor - a
backup, a duplication, a replication, an image cleanup, ... - keyed by
its job id within one primary. NetBackup keeps finished jobs only for a
few days (KEEP_JOBS_HOURS, 78 by default); blt keeps them for good.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# initial - first ever run: every job changed in a recent window (which
#           NetBackup's own job retention usually cuts shorter)
# delta   - every job changed since the watermark, less an overlap
# full    - forced: every job changed in the history limit
NetBackupRunMode = Literal["initial", "delta", "full"]
NetBackupRunStatus = Literal["running", "succeeded", "failed"]


class NetBackupJobIn(BaseModel):
    """One NetBackup job as of one collection."""

    job_id: int = Field(description="NetBackup's job id, unique within its primary.")
    parent_job_id: int | None = Field(
        default=None,
        description="The job this one belongs to; NetBackup gives a job its own id as parent "
        "when it has none.",
    )
    job_type: str | None = Field(
        default=None,
        description="What sort of work, in NetBackup's words: BACKUP, DUPLICATE, REPLICATE, "
        "IMPORT, RESTORE, IMAGEDELETE, DBBACKUP, ...",
    )
    state: str = Field(
        description="Where it is: QUEUED, ACTIVE, REQUEUED, SUSPENDED, INCOMPLETE or DONE."
    )
    status: int | None = Field(
        default=None,
        description="NetBackup's status code, final once DONE: 0 succeeded, 1 partly, others "
        "failed (the code says why).",
    )
    is_active: bool = Field(description="Not DONE yet.")
    policy_name: str | None = Field(default=None, description="The policy that ran it.")
    policy_type: str | None = Field(
        default=None, description="The policy's type: STANDARD, VMWARE, ORACLE, ..."
    )
    schedule_name: str | None = Field(default=None, description="The policy schedule.")
    schedule_type: str | None = Field(
        default=None, description="FULL, DIFFERENTIAL_INCREMENTAL_BACKUP, ..."
    )
    client_name: str | None = Field(default=None, description="The client it ran for.")
    media_server: str | None = Field(
        default=None, description="The media server it wrote to (destination)."
    )
    storage_unit: str | None = Field(
        default=None, description="The storage unit it wrote to (destination)."
    )
    source_media_server: str | None = Field(
        default=None, description="For a copy: the media server it read from."
    )
    source_storage_unit: str | None = Field(
        default=None, description="For a copy: the storage unit it read from."
    )
    backup_id: str | None = Field(
        default=None,
        description="The image it made or worked on - the key to the catalog, and across "
        "primaries for a replicated image.",
    )
    try_number: int | None = Field(default=None, description="Which try: above 1 for a retry.")
    start_time: AwareDatetime | None = Field(default=None, description="When it started.")
    end_time: AwareDatetime | None = Field(default=None, description="When it ended, once DONE.")
    last_update_time: AwareDatetime | None = Field(
        default=None, description="When NetBackup last changed it."
    )
    kilobytes_transferred: int | None = Field(
        default=None, description="KiB written, as NetBackup counts it (may be an estimate)."
    )
    kilobytes_data_transferred: int | None = Field(
        default=None, description="KiB actually sent (NetBackup 10.5 / API 12.0 and later)."
    )
    kilobytes_to_transfer: int | None = Field(
        default=None, description="KiB it expects to write in all."
    )
    number_of_files: int | None = Field(default=None, description="Files it covered.")
    percent_complete: int | None = Field(default=None, description="0-100.")
    raw: dict[str, Any] = Field(
        default_factory=dict, description="NetBackup's job record, untouched."
    )


class NetBackupJobBatch(BaseModel):
    """Jobs collected in one go, sent during a run."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "run_id": 12,
                    "collected_at": "2026-11-08T14:02:00Z",
                    "jobs": [
                        {
                            "job_id": 48211,
                            "job_type": "BACKUP",
                            "state": "DONE",
                            "status": 0,
                            "is_active": False,
                            "policy_name": "vm-gold",
                            "client_name": "app01.example.com",
                            "storage_unit": "msdp-stu-01",
                            "backup_id": "app01.example.com_1794150000",
                            "start_time": "2026-11-08T13:00:00Z",
                            "end_time": "2026-11-08T13:21:00Z",
                            "last_update_time": "2026-11-08T13:21:00Z",
                        }
                    ],
                }
            ]
        }
    )

    run_id: int | None = Field(
        default=None, description="The run these belong to (`POST .../runs`)."
    )
    collected_at: AwareDatetime = Field(description="When the collector read them.")
    jobs: list[NetBackupJobIn] = Field(description="The jobs.")


class NetBackupJobOut(NetBackupJobIn):
    """A NetBackup job as blt holds it: its latest state."""

    model_config = ConfigDict(from_attributes=True)

    first_seen_at: AwareDatetime = Field(description="When blt first collected it.")
    last_collected_at: AwareDatetime = Field(description="When blt last collected it.")
    last_seen_run_id: int | None = Field(
        default=None, description="The run that last collected it."
    )


class NetBackupRunStart(BaseModel):
    """What a collector says when it opens a run."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"mode": "delta", "started_at": "2026-11-08T14:02:00Z", "lookup_seconds": 2700}
            ]
        }
    )

    mode: NetBackupRunMode = Field(description="initial, delta or full.")
    started_at: AwareDatetime = Field(
        description="When the collector started; a succeeded run's start is the next watermark."
    )
    lookup_seconds: int = Field(
        ge=0, description="How far before `started_at` it looked for changes."
    )


class NetBackupRunFinish(BaseModel):
    """How a run ended."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"status": "succeeded", "finished_at": "2026-11-08T14:03:10Z", "jobs_collected": 37}
            ]
        }
    )

    status: Literal["succeeded", "failed"] = Field(description="succeeded or failed.")
    finished_at: AwareDatetime = Field(description="When it ended.")
    jobs_collected: int = Field(default=0, ge=0, description="Jobs sent.")
    error: str | None = Field(default=None, description="Why it failed.")


class NetBackupRunOut(BaseModel):
    """A collection run, as blt holds it."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="The run's id.")
    mode: NetBackupRunMode = Field(description="initial, delta or full.")
    status: NetBackupRunStatus = Field(description="running, succeeded or failed.")
    started_at: AwareDatetime = Field(description="When the collector started.")
    finished_at: AwareDatetime | None = Field(default=None, description="When it ended.")
    lookup_seconds: int = Field(description="How far back it looked.")
    jobs_collected: int = Field(description="Jobs it sent.")
    error: str | None = Field(default=None, description="Why it failed.")


class NetBackupLastRun(BaseModel):
    """Where a primary's collection got to."""

    primary: str = Field(description="blt's name for the primary.")
    watermark: AwareDatetime | None = Field(
        default=None,
        description="When the last succeeded run started; none means do an initial run.",
    )
    last_run: NetBackupRunOut | None = Field(default=None, description="The most recent run.")
