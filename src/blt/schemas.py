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


RunMode = Literal["full", "delta"]
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


class LastRun(BaseModel):
    """`watermark` is when the most recent *successful* run started - the
    point a delta run has to reach back to. None means nothing has ever
    been collected for this CommCell, i.e. do a full run."""

    commcell: str
    watermark: AwareDatetime | None = None
    last_run: RunOut | None = None
