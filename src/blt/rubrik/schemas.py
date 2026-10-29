"""The wire contract for Rubrik, between its collector and the API.

Same idea as blt.schemas for Commvault: both halves import these, so
what the collector sends is checked by the model the API reads it with.
Nothing here needs the Rubrik SDK or Postgres.

A Rubrik "job" is an RSC activity series: one backup, recovery,
replication or other run. It is keyed by `series_key` (RSC's global id
for it, see rubrik_sdk.ActivitySeries.key) within one RSC account.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# initial - first ever run: whatever changed in a recent window
# delta   - whatever changed since the watermark (less an overlap)
# full    - forced: whatever changed in the whole history limit
RubrikRunMode = Literal["initial", "delta", "full"]
RubrikRunStatus = Literal["running", "succeeded", "failed"]


class RubrikJobIn(BaseModel):
    series_key: str = Field(
        description=(
            "blt's key for the job: RSC's fid, or cluster id and series id when there is none."
        )
    )
    fid: str | None = None
    activity_series_id: str | None = None
    cluster_id: str | None = None
    cluster_name: str | None = None
    object_id: str | None = None
    object_name: str | None = None
    object_type: str | None = None
    location: str | None = None
    activity_type: str | None = None
    status: str | None = Field(
        default=None,
        description="RSC's status for the job's latest activity: Success, Failure, Running, ...",
    )
    is_active: bool = Field(default=False, description="Still running or queued.")
    severity: str | None = None
    progress: str | None = None
    percent_complete: float | None = Field(default=None, description="`progress` as a number.")
    sla_domain_name: str | None = None
    last_activity_message: str | None = Field(
        default=None, description="The latest event's message, as RSC words it."
    )
    failure_reason: str | None = Field(default=None, description="Why the job failed, if it did.")
    cause_error_code: str | None = None
    cause_error_message: str | None = None
    cause_error_reason: str | None = None
    cause_error_remedy: str | None = None
    attempt_number: int | None = None
    is_on_demand: bool | None = Field(
        default=None, description="Run by hand rather than by its SLA domain."
    )
    data_transferred: int | None = Field(default=None, description="Bytes.")
    logical_size: int | None = Field(default=None, description="Bytes.")
    effective_throughput: int | None = Field(default=None, description="Bytes per second.")
    start_time: AwareDatetime | None = None
    last_updated: AwareDatetime | None = Field(
        default=None,
        description="When RSC last changed the job. RSC has no end time: for a finished job, "
        "this is when it ended.",
    )
    raw: dict[str, Any] = Field(
        default_factory=dict, description="RSC's activity series record, untouched."
    )


class RubrikJobBatch(BaseModel):
    run_id: int | None = Field(
        default=None, description="The run this batch belongs to: the `id` from `POST .../runs`."
    )
    collected_at: AwareDatetime = Field(
        description=(
            "When this was read from the source. Nothing older than what is already stored "
            "overwrites it."
        )
    )
    jobs: list[RubrikJobIn]


class RubrikJobOut(RubrikJobIn):
    model_config = ConfigDict(from_attributes=True)

    account_id: int
    first_seen_at: AwareDatetime
    last_collected_at: AwareDatetime
    last_seen_run_id: int | None = None


class RubrikRunStart(BaseModel):
    mode: RubrikRunMode = Field(
        description=(
            "initial: first ever run, a recent window. delta: changes since the watermark, less an"
            " overlap. full: changes within the whole history limit."
        )
    )
    started_at: AwareDatetime
    lookup_seconds: int = Field(
        description=(
            "How far back the run asked for changes: everything updated after started_at minus "
            "this."
        )
    )


class RubrikRunFinish(BaseModel):
    status: Literal["succeeded", "failed"]
    finished_at: AwareDatetime
    jobs_collected: int = 0
    error: str | None = None


class RubrikRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    mode: RubrikRunMode
    status: RubrikRunStatus
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    lookup_seconds: int
    jobs_collected: int
    error: str | None = None


class RubrikLastRun(BaseModel):
    """`watermark` is when the most recent successful run started - what
    a delta run reaches back to. None: never collected, do an initial
    run."""

    account: str
    watermark: AwareDatetime | None = None
    last_run: RubrikRunOut | None = None
