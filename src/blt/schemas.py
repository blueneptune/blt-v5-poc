"""The wire contract between the collector and the API.

Both halves import these, so a payload the collector builds is validated
by the same model the API parses it with. Nothing here knows about
Commvault's own JSON shape (see blt.commvault.models) or about Postgres
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

    job_id: int
    status: str
    job_type: str | None = None
    operation: str | None = None
    backup_level: str | None = None
    app_type: str | None = None
    client_id: int | None = None
    client_name: str | None = None
    subclient_name: str | None = None
    backupset_name: str | None = None
    instance_name: str | None = None
    storage_policy: str | None = None
    percent_complete: float | None = None
    start_time: AwareDatetime | None = None
    end_time: AwareDatetime | None = None
    last_update_time: AwareDatetime | None = None
    elapsed_seconds: int | None = None
    size_of_application: int | None = None
    size_of_media: int | None = None
    total_files: int | None = None
    failed_files: int | None = None
    pending_reason: str | None = None
    is_aged: bool | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class JobBatch(BaseModel):
    run_id: int | None = None
    collected_at: AwareDatetime
    jobs: list[JobIn]


class JobPatch(BaseModel):
    """A change to one already-known job - today only used to mark a job
    MISSING_STATUS, since anything Commvault can still describe goes
    through the batch upsert instead."""

    status: str
    collected_at: AwareDatetime
    run_id: int | None = None


class JobOut(JobIn):
    model_config = ConfigDict(from_attributes=True)

    commcell_id: int
    state: str
    is_active: bool
    first_seen_at: AwareDatetime
    last_collected_at: AwareDatetime
    last_seen_run_id: int | None = None


class UpsertResult(BaseModel):
    received: int
    written: int


# initial  - first ever run: what is active plus a short recent window
# delta    - everything since the watermark
# full     - forced (--full): the whole history in one query
# backfill - older history, fetched a time slice at a time
# inventory - not jobs at all: what the source says exists (see ObjectIn)
RunMode = Literal["initial", "full", "delta", "backfill", "inventory"]
RunStatus = Literal["running", "succeeded", "failed"]


class RunStart(BaseModel):
    mode: RunMode
    started_at: AwareDatetime
    lookup_seconds: int


class RunFinish(BaseModel):
    status: Literal["succeeded", "failed"]
    finished_at: AwareDatetime
    jobs_collected: int = 0
    jobs_reconciled: int = 0
    jobs_missing: int = 0
    error: str | None = None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    commcell_id: int
    mode: RunMode
    status: RunStatus
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    lookup_seconds: int
    jobs_collected: int
    jobs_reconciled: int
    jobs_missing: int
    error: str | None = None


ObjectKind = Literal["client", "instance", "database"]


class ObjectIn(BaseModel):
    """One thing a source says exists, as that source describes it.

    This is the observation layer of docs/asset-refinery.md and nothing
    more: no merging, no judgement. `source_key` is the source's own
    stable identifier for the thing, unique per (source, kind). A parent
    is named by its own (kind, source_key) and must already have been
    sent - clients before instances before databases.
    """

    kind: ObjectKind
    source_key: str
    name: str
    parent_kind: ObjectKind | None = None
    parent_key: str | None = None
    # What sort of thing within its kind: "SQL Server", "File System", ...
    app_type: str | None = None
    # Protection evidence, where the source reports it per object.
    last_backup_at: AwareDatetime | None = None
    last_backup_job_id: int | None = None
    # The last *full* backup that included it, where the source says.
    last_full_job_id: int | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class ObjectBatch(BaseModel):
    run_id: int
    collected_at: AwareDatetime
    objects: list[ObjectIn]


class ObjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: ObjectKind
    source_key: str
    name: str
    parent_id: str | None = None
    app_type: str | None = None
    last_backup_at: AwareDatetime | None = None
    last_backup_job_id: int | None = None
    present: bool
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    attributes: dict[str, Any] = Field(default_factory=dict)


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

    client: str | None = None
    instance: str
    app_type: str | None = None
    job_id: int | None = None
    job_status: str | None = None
    job_ended_at: AwareDatetime | None = None
    discovered: int
    backed_up: int
    reported_backed_up: int | None = None
    reported_skipped: int | None = None
    missed: list[str] = Field(default_factory=list)
    unconfirmed: list[str] = Field(default_factory=list)
    notes: str = ""
    observed_at: AwareDatetime | None = None


class ValidationRow(BaseModel):
    client: str | None = None
    instance: str | None = None
    kind: ObjectKind
    name: str
    app_type: str | None = None
    verdict: Verdict
    detail: str
    last_backup_at: AwareDatetime | None = None
    last_backup_job_id: int | None = None
    last_backup_job_status: str | None = None


class ValidationReport(BaseModel):
    """Protection as judged from one source's own account of itself.

    Today the only witness to what exists is the backup product, so this
    can find what that product knows about and has not protected. It
    cannot find what the product has never seen; `inventory_at` and
    `sources` are here so a reader can tell how much the answer rests on.
    """

    commcell: str
    sources: list[str]
    inventory_at: AwareDatetime | None = None
    max_age_hours: float
    counts: dict[str, int]
    problems: int
    rows: list[ValidationRow]


class BackfillState(BaseModel):
    """How far back a CommCell's history has been collected.

    `resume_from` is where the next backfill chunk should end: the oldest
    point already covered, whether by a backfill or by ordinary runs.
    None means nothing has been collected yet, so there is nothing to
    backfill behind."""

    commcell: str
    resume_from: AwareDatetime | None = None
    backfilled_to: AwareDatetime | None = None
    complete: bool = False


class BackfillUpdate(BaseModel):
    backfilled_to: AwareDatetime
    complete: bool = False


class LastRun(BaseModel):
    """`watermark` is when the most recent *successful* collection run
    (initial, delta or full - not backfill) started - the
    point a delta run has to reach back to. None means nothing has ever
    been collected for this CommCell, i.e. do a full run."""

    commcell: str
    watermark: AwareDatetime | None = None
    last_run: RunOut | None = None
