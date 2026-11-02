"""The wire contract between the collector and the API.

Both halves import these, so a payload the collector builds is validated
by the same model the API parses it with. Nothing here knows about
Commvault's own JSON shape (the commvault-sdk package) or about Postgres
(see blt.api.models) - it is only what travels between the two.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# Written by the collector when a job the database still believes is
# active can no longer be found in the CommCell at all (pruned, or the
# CommServe DB was restored to before it existed). Not a Commvault status.
MISSING_STATUS = "Not found in CommCell"

# Commvault's status string (lower-cased) -> (our normalised state, is it
# still going to change?). The raw string is always stored too; this only
# exists so "give me everything still in flight" is one indexed boolean
# instead of a list of vendor strings every caller has to know.
_STATES: dict[str, tuple[str, bool]] = {
    "running": ("running", True),
    "queued": ("queued", True),
    "waiting": ("waiting", True),
    "pending": ("pending", True),
    "suspended": ("suspended", True),
    "kill pending": ("stopping", True),
    "suspend pending": ("stopping", True),
    "interrupt pending": ("stopping", True),
    "completed": ("completed", False),
    "completed w/ one or more errors": ("completed_with_errors", False),
    "completed w/ one or more warnings": ("completed_with_warnings", False),
    "committed": ("committed", False),
    "failed": ("failed", False),
    "failed to start": ("failed", False),
    "killed": ("killed", False),
    "no run": ("no_run", False),
    MISSING_STATUS.lower(): ("missing", False),
}


def classify_status(status: str) -> tuple[str, bool]:
    """(state, is_active) for a raw Commvault status string.

    A status this table doesn't know is treated as still active: the cost
    of being wrong that way is one extra per-job lookup each run, where
    the cost of wrongly calling a live job finished is a row that never
    gets refreshed again.
    """
    return _STATES.get(status.strip().lower(), ("unknown", True))


class JobIn(BaseModel):
    """One job as of one collection. `raw` is Commvault's jobSummary
    untouched, so a field nobody modelled yet is still recoverable."""

    job_id: int = Field(description="Commvault's job id.")
    status: str = Field(description="Commvault status, as Commvault words it.")
    job_type: str | None = Field(
        default=None,
        description="Commvault's job type: Backup, Restore, Data Aging, Auxiliary Copy, ...",
    )
    operation: str | None = Field(
        default=None,
        description="What the job did, as Command Center shows it (localizedOperationName).",
    )
    backup_level: str | None = Field(
        default=None,
        description="Full, Incremental, Differential, Synthetic Full, ... Backups only.",
    )
    app_type: str | None = Field(
        default=None, description="The agent: File System, SQL Server, Virtual Server, ..."
    )
    client_id: int | None = Field(default=None, description="Commvault's id for the client.")
    client_name: str | None = Field(default=None, description="The client the job ran for.")
    subclient_name: str | None = Field(
        default=None, description="The subclient: what was backed up, and how."
    )
    backupset_name: str | None = Field(
        default=None, description="The backup set the subclient is in."
    )
    instance_name: str | None = Field(
        default=None, description="The agent instance (a SQL Server instance, a hypervisor, ...)."
    )
    storage_policy: str | None = Field(
        default=None, description="The storage policy (plan) the job wrote to."
    )
    percent_complete: float | None = Field(
        default=None, description="0 to 100, as Commvault reports it."
    )
    start_time: AwareDatetime | None = Field(default=None, description="When the job started.")
    end_time: AwareDatetime | None = Field(
        default=None, description="When it ended. Null while it runs."
    )
    last_update_time: AwareDatetime | None = Field(
        default=None, description="When Commvault last changed the job."
    )
    elapsed_seconds: int | None = Field(
        default=None, description="Run time so far, or in total once finished."
    )
    size_of_application: int | None = Field(
        default=None, description="Bytes of source data the job covered (application size)."
    )
    size_of_media: int | None = Field(
        default=None,
        description=("Bytes the job wrote to backup storage, after compression and deduplication."),
    )
    total_files: int | None = Field(default=None, description="Files the job processed.")
    failed_files: int | None = Field(default=None, description="Files it could not back up.")
    pending_reason: str | None = Field(
        default=None,
        description="Why the job is waiting or failed, in Commvault's words.",
    )
    is_aged: bool | None = Field(
        default=None, description="Its data has been aged off (deleted by retention)."
    )
    raw: dict[str, Any] = Field(
        default_factory=dict, description="Commvault's jobSummary for the job, untouched."
    )


class JobBatch(BaseModel):
    """Jobs collected in one go, sent during a run."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "run_id": 1234,
                    "collected_at": "2026-10-29T14:01:00Z",
                    "jobs": [
                        {
                            "job_id": 4711,
                            "status": "Completed",
                            "job_type": "Backup",
                            "backup_level": "Incremental",
                            "app_type": "File System",
                            "client_name": "fs-01",
                            "subclient_name": "default",
                            "storage_policy": "Server Plan",
                            "percent_complete": 100,
                            "start_time": "2026-10-29T13:00:00Z",
                            "end_time": "2026-10-29T13:12:30Z",
                            "size_of_application": 52428800,
                            "size_of_media": 10485760,
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
    jobs: list[JobIn] = Field(description="The jobs, each as it was at collected_at.")


class JobPatch(BaseModel):
    """A change to one already-known job - today only used to mark a job
    MISSING_STATUS, since anything Commvault can still describe goes
    through the batch upsert instead."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "Not found in CommCell",
                    "collected_at": "2026-10-29T14:01:30Z",
                    "run_id": 1234,
                }
            ]
        }
    )

    status: str = Field(description='The new status - today only "Not found in CommCell".')
    collected_at: AwareDatetime = Field(description="When the collector found this out.")
    run_id: int | None = Field(default=None, description="The run that found it.")


class JobOut(JobIn):
    """A job as blt holds it: the latest state it was seen in, plus blt's own fields."""

    model_config = ConfigDict(from_attributes=True)

    commcell_id: int = Field(description="blt's id for the CommCell.")
    state: str = Field(
        description=(
            "blt's state for `status`: running, queued, waiting, pending, suspended, stopping, "
            "completed, completed_with_errors, completed_with_warnings, committed, failed, killed,"
            " no_run, missing (the CommCell no longer knows the job), or unknown."
        )
    )
    is_active: bool = Field(
        description="Still going to change. A status blt does not recognise counts as active."
    )
    first_seen_at: AwareDatetime = Field(description="When blt first collected the job.")
    last_collected_at: AwareDatetime = Field(
        description="When this state of the job was read from the CommCell."
    )
    last_seen_run_id: int | None = Field(
        default=None, description="The last run that collected the job."
    )


class UpsertResult(BaseModel):
    """What a batch did."""

    received: int = Field(description="Items in the batch.")
    written: int = Field(
        description=(
            "Items inserted or changed. Fewer than received when some were already stored as newer."
        )
    )


# initial  - first ever run: what is active plus a short recent window
# delta    - everything since the watermark
# full     - forced (--full): the whole history in one query
# backfill - older history, fetched a time slice at a time
# inventory - not jobs at all: what the source says exists (see ObjectIn)
RunMode = Literal["initial", "full", "delta", "backfill", "inventory"]
RunStatus = Literal["running", "succeeded", "failed"]


class RunStart(BaseModel):
    """Opens a run."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"mode": "delta", "started_at": "2026-10-29T14:00:00Z", "lookup_seconds": 3900}
            ]
        }
    )

    mode: RunMode = Field(
        description=(
            "initial: first ever run, active jobs plus a short recent window. delta: everything "
            "since the watermark. full: the whole history in one query. backfill: older history, a"
            " time slice at a time. inventory: what the CommCell says exists, not jobs."
        )
    )
    started_at: AwareDatetime = Field(
        description=(
            "When the run started. A succeeded collection run's start becomes the watermark."
        )
    )
    lookup_seconds: int = Field(description="How far before started_at the run looked for jobs.")


class RunFinish(BaseModel):
    """How a run ended."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "succeeded",
                    "finished_at": "2026-10-29T14:02:10Z",
                    "jobs_collected": 42,
                    "jobs_reconciled": 3,
                    "jobs_missing": 0,
                }
            ]
        }
    )

    status: Literal["succeeded", "failed"] = Field(
        description=(
            "succeeded moves the watermark (collection runs) or settles the "
            "inventory; failed changes neither."
        )
    )
    finished_at: AwareDatetime = Field(description="When the run finished.")
    jobs_collected: int = Field(default=0, description="Jobs stored by the run.")
    jobs_reconciled: int = Field(default=0, description="Active jobs checked on one by one.")
    jobs_missing: int = Field(
        default=0, description="Jobs marked as no longer known to the CommCell."
    )
    error: str | None = Field(default=None, description="Why a failed run failed.")


class RunOut(BaseModel):
    """One collection or inventory run."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="The run's id: send it with each batch, and to finish the run.")
    commcell_id: int = Field(description="blt's id for the CommCell.")
    mode: RunMode = Field(description="What kind of run it is (see RunStart).")
    status: RunStatus = Field(description="running until finished; then succeeded or failed.")
    started_at: AwareDatetime = Field(description="When the collector started the run.")
    finished_at: AwareDatetime | None = Field(
        default=None, description="When it finished. Null while running."
    )
    lookup_seconds: int = Field(description="How far before started_at the run looked for jobs.")
    jobs_collected: int = Field(description="Jobs stored from this run.")
    jobs_reconciled: int = Field(description="Active jobs checked on one by one.")
    jobs_missing: int = Field(description="Jobs marked as no longer known to the CommCell.")
    error: str | None = Field(default=None, description="Why a failed run failed.")


ObjectKind = Literal["client", "instance", "database"]


class ObjectIn(BaseModel):
    """One thing a source says exists, as that source describes it.

    This is the observation layer of docs/asset-refinery.md and nothing
    more: no merging, no judgement. `source_key` is the source's own
    stable identifier for the thing, unique per (source, kind). A parent
    is named by its own (kind, source_key) and must already have been
    sent - clients before instances before databases.
    """

    kind: ObjectKind = Field(description="client, instance or database.")
    source_key: str = Field(description="The source's own stable id for it, unique per kind.")
    name: str = Field(description="Its name, as the source shows it.")
    parent_kind: ObjectKind | None = Field(
        default=None, description="The parent's kind. The parent must already have been sent."
    )
    parent_key: str | None = Field(default=None, description="The parent's source_key.")
    app_type: str | None = Field(
        default=None,
        description='What sort of thing within its kind: "SQL Server", "File System", ...',
    )
    last_backup_at: AwareDatetime | None = Field(
        default=None, description="Its last backup, where the source reports one per object."
    )
    last_backup_job_id: int | None = Field(
        default=None, description="The job that took that backup."
    )
    last_full_job_id: int | None = Field(
        default=None,
        description="The last full backup job that included it, where the source says.",
    )
    attributes: dict[str, Any] = Field(
        default_factory=dict, description="Anything else the source says about it."
    )


class ObjectBatch(BaseModel):
    """Inventory sent during an inventory run."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "run_id": 1235,
                    "collected_at": "2026-10-29T14:05:00Z",
                    "objects": [
                        {"kind": "client", "source_key": "12", "name": "sql-01"},
                        {
                            "kind": "instance",
                            "source_key": "34",
                            "name": "sql-01\\MSSQLSERVER",
                            "parent_kind": "client",
                            "parent_key": "12",
                            "app_type": "SQL Server",
                        },
                        {
                            "kind": "database",
                            "source_key": "34/sales",
                            "name": "sales",
                            "parent_kind": "instance",
                            "parent_key": "34",
                            "app_type": "SQL Server",
                        },
                    ],
                }
            ]
        }
    )

    run_id: int = Field(description="The inventory run this batch belongs to.")
    collected_at: AwareDatetime = Field(description="When this was read from the source.")
    objects: list[ObjectIn] = Field(
        description="Clients, then instances, then databases: a parent before its children."
    )


class ObjectOut(BaseModel):
    """Something the CommCell says exists, as of its latest inventory."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="blt's id for the object (a UUID).")
    kind: ObjectKind = Field(description="client, instance or database.")
    source_key: str = Field(description="The source's own stable id for it, unique per kind.")
    name: str = Field(description="Its name, as the source shows it.")
    parent_id: str | None = Field(
        default=None,
        description="blt's id for its parent: a database's instance, an instance's client.",
    )
    app_type: str | None = Field(
        default=None,
        description='What sort of thing within its kind: "SQL Server", "File System", ...',
    )
    last_backup_at: AwareDatetime | None = Field(
        default=None, description="Its last backup, where the source reports one per object."
    )
    last_backup_job_id: int | None = Field(
        default=None, description="The job that took that backup."
    )
    present: bool = Field(
        description="Listed by the latest inventory. False: it was known, and is gone."
    )
    first_seen_at: AwareDatetime = Field(description="When an inventory first listed it.")
    last_seen_at: AwareDatetime = Field(description="When an inventory last listed it.")
    attributes: dict[str, Any] = Field(
        default_factory=dict, description="Anything else the source says about it."
    )


# ok           backed up within the allowed age
# stale        backed up, but longer ago than that
# unprotected  the source knows it exists and has no backup of it
# empty        an instance with no databases known under it at all
# unverified   exists and is in backup content, but the source offers no
#              per-object backup time to check (SQL Server system databases)
# gone         was known, and the latest inventory no longer lists it
Verdict = Literal["ok", "stale", "unprotected", "empty", "unverified", "gone"]
PROBLEM_VERDICTS: frozenset[str] = frozenset({"stale", "unprotected", "empty"})


class InstanceReportRow(BaseModel):
    """One database instance and its most recent full backup: how many
    databases the source knows on it, how many that backup took, and
    which ones it missed.

    `backed_up` counts the known databases whose last full is this job,
    by name. `reported_backed_up` / `reported_skipped` are the backup
    product's own totals for the job, kept alongside as a cross-check
    rather than trusted on their own. System databases cannot be checked
    by name (the source gives them no per-database job), so they are
    counted in `discovered` and reported separately in `unconfirmed`.
    """

    model_config = ConfigDict(from_attributes=True)

    client: str | None = Field(default=None, description="The client the instance is on.")
    instance: str = Field(description="The instance (a SQL Server instance).")
    app_type: str | None = Field(default=None, description="The agent, e.g. SQL Server.")
    job_id: int | None = Field(
        default=None, description="Its most recent full backup job. Null or 0: never backed up."
    )
    job_status: str | None = Field(default=None, description="That job's Commvault status.")
    job_ended_at: AwareDatetime | None = Field(default=None, description="When that job ended.")
    discovered: int = Field(description="Databases the source knows on the instance.")
    backed_up: int = Field(description="Of those, how many this job backed up (matched by name).")
    reported_backed_up: int | None = Field(
        default=None, description="Commvault's own count of databases the job backed up."
    )
    reported_skipped: int | None = Field(
        default=None, description="Commvault's own count of databases the job skipped."
    )
    missed: list[str] = Field(
        default_factory=list,
        description="Databases known on the instance that this job did not back up.",
    )
    unconfirmed: list[str] = Field(
        default_factory=list,
        description="Databases that cannot be checked by name (system databases).",
    )
    notes: str = Field(
        default="", description="Anything else worth knowing about this row, in words."
    )
    observed_at: AwareDatetime | None = Field(
        default=None, description="When the inventory run that produced this row finished."
    )


class ValidationRow(BaseModel):
    """One object and the verdict on its protection."""

    client: str | None = Field(default=None, description="The client.")
    instance: str | None = Field(
        default=None, description="The instance, for instances and databases."
    )
    kind: ObjectKind = Field(description="client, instance or database.")
    name: str = Field(description="Its name, as the source shows it.")
    app_type: str | None = Field(
        default=None,
        description='What sort of thing within its kind: "SQL Server", "File System", ...',
    )
    verdict: Verdict = Field(
        description=(
            "ok: backed up within the allowed age. stale: backed up, but longer ago. unprotected: "
            "known to the source, with no backup. empty: an instance with no databases known under"
            " it. unverified: in backup content, but the source gives no per-object backup time "
            "(SQL Server system databases). gone: the latest inventory no longer lists it."
        )
    )
    detail: str = Field(description="The verdict, explained in words.")
    last_backup_at: AwareDatetime | None = Field(
        default=None, description="Its last backup, where known."
    )
    last_backup_job_id: int | None = Field(
        default=None, description="The job that took that backup."
    )
    last_backup_job_status: str | None = Field(
        default=None, description="That job's Commvault status."
    )


class ValidationReport(BaseModel):
    """Protection as judged from one source's own account of itself.

    Today the only witness to what exists is the backup product, so this
    can find what that product knows about and has not protected. It
    cannot find what the product has never seen; `inventory_at` and
    `sources` are here so a reader can tell how much the answer rests on.
    """

    commcell: str = Field(description="blt's name for the CommCell.")
    sources: list[str] = Field(
        description="What the verdicts rest on: today only the CommCell's own inventory."
    )
    inventory_at: AwareDatetime | None = Field(
        default=None, description="When the latest inventory run finished. Null: never inventoried."
    )
    max_age_hours: float = Field(description="The age past which a backup counted as stale.")
    counts: dict[str, int] = Field(description="Rows per verdict.")
    problems: int = Field(description="Rows that are stale, unprotected or empty.")
    rows: list[ValidationRow] = Field(
        description="One row per object judged, by client, instance, kind and name."
    )


class BackfillState(BaseModel):
    """How far back a CommCell's history has been collected.

    `resume_from` is where the next backfill chunk should end: the oldest
    point already covered, whether by a backfill or by ordinary runs.
    None means nothing has been collected yet, so there is nothing to
    backfill behind."""

    commcell: str = Field(description="blt's name for the CommCell.")
    resume_from: AwareDatetime | None = Field(
        default=None,
        description=(
            "Where the next backfill chunk should end: the oldest point already "
            "covered. Null: nothing collected yet."
        ),
    )
    backfilled_to: AwareDatetime | None = Field(
        default=None, description="How far back a backfill has reached. Null: no backfill has run."
    )
    complete: bool = Field(
        default=False, description="The start of the CommCell's history has been reached."
    )


class BackfillUpdate(BaseModel):
    """How far back a backfill has now collected."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"backfilled_to": "2026-09-01T00:00:00Z", "complete": False}]
        }
    )

    backfilled_to: AwareDatetime = Field(description="The oldest point now collected.")
    complete: bool = Field(
        default=False, description="The start of the CommCell's history has been reached."
    )


class LastRun(BaseModel):
    """`watermark` is when the most recent *successful* collection run
    (initial, delta or full - not backfill) started - the
    point a delta run has to reach back to. None means nothing has ever
    been collected for this CommCell, i.e. do a full run."""

    commcell: str = Field(description="blt's name for the CommCell.")
    watermark: AwareDatetime | None = Field(
        default=None,
        description=("When the last successful collection run started. Null: never collected."),
    )
    last_run: RunOut | None = Field(
        default=None, description="The most recent run of any kind, whatever its outcome."
    )
