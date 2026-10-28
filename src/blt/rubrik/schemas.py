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
    series_key: str
    fid: str | None = None
    activity_series_id: str | None = None
    cluster_id: str | None = None
    cluster_name: str | None = None
    object_id: str | None = None
    object_name: str | None = None
    object_type: str | None = None
    location: str | None = None
    activity_type: str | None = None
    status: str | None = None
    is_active: bool = False
    severity: str | None = None
    progress: str | None = None
    sla_domain_name: str | None = None
    start_time: AwareDatetime | None = None
    last_updated: AwareDatetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class RubrikJobBatch(BaseModel):
    run_id: int | None = None
    collected_at: AwareDatetime
    jobs: list[RubrikJobIn]


class RubrikJobOut(RubrikJobIn):
    model_config = ConfigDict(from_attributes=True)

    account_id: int
    first_seen_at: AwareDatetime
    last_collected_at: AwareDatetime
    last_seen_run_id: int | None = None


class RubrikRunStart(BaseModel):
    mode: RubrikRunMode
    started_at: AwareDatetime
    # How far back this run asked for changes: everything updated after
    # started_at minus this.
    lookup_seconds: int


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
