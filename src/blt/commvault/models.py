"""Commvault's own job JSON, and the translation into blt's JobIn.

Field names follow the `jobSummary` object the REST API returns from
POST /Jobs and GET /Job/{id} (the same one cvpysdk's JobController
reads). Everything except jobId is optional: which fields are present
varies by job type and CommServe version, and the untouched summary is
kept in JobIn.raw regardless.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from blt.schemas import JobIn


class _Subclient(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    client_id: int | None = Field(default=None, alias="clientId")
    client_name: str | None = Field(default=None, alias="clientName")
    subclient_name: str | None = Field(default=None, alias="subclientName")
    backupset_name: str | None = Field(default=None, alias="backupsetName")
    instance_name: str | None = Field(default=None, alias="instanceName")


class _StoragePolicy(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str | None = Field(default=None, alias="storagePolicyName")


class JobSummary(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    job_id: int = Field(alias="jobId")
    status: str = "Unknown"
    job_type: str | None = Field(default=None, alias="jobType")
    operation: str | None = Field(default=None, alias="localizedOperationName")
    backup_level: str | None = Field(default=None, alias="backupLevelName")
    app_type: str | None = Field(default=None, alias="appTypeName")
    percent_complete: float | None = Field(default=None, alias="percentComplete")
    job_start_time: int | None = Field(default=None, alias="jobStartTime")
    job_end_time: int | None = Field(default=None, alias="jobEndTime")
    last_update_time: int | None = Field(default=None, alias="lastUpdateTime")
    job_elapsed_time: int | None = Field(default=None, alias="jobElapsedTime")
    size_of_application: int | None = Field(default=None, alias="sizeOfApplication")
    size_of_media: int | None = Field(default=None, alias="sizeOfMediaOnDisk")
    total_files: int | None = Field(default=None, alias="totalNumOfFiles")
    failed_files: int | None = Field(default=None, alias="totalFailedFiles")
    pending_reason: str | None = Field(default=None, alias="pendingReason")
    is_aged: bool | None = Field(default=None, alias="isAged")
    dest_client_name: str | None = Field(default=None, alias="destClientName")
    subclient: _Subclient = Field(default_factory=_Subclient)
    storage_policy: _StoragePolicy = Field(default_factory=_StoragePolicy, alias="storagePolicy")


def _from_epoch(seconds: int | None) -> datetime | None:
    """Commvault reports times as epoch seconds and uses 0 for "not yet"
    (e.g. the end time of a job that is still running)."""
    if not seconds:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC)


def job_in_from_summary(raw: dict[str, Any]) -> JobIn:
    summary = JobSummary.model_validate(raw)
    return JobIn(
        job_id=summary.job_id,
        status=summary.status,
        job_type=summary.job_type,
        operation=summary.operation,
        backup_level=summary.backup_level,
        app_type=summary.app_type,
        client_id=summary.subclient.client_id,
        client_name=summary.subclient.client_name or summary.dest_client_name,
        subclient_name=summary.subclient.subclient_name,
        backupset_name=summary.subclient.backupset_name,
        instance_name=summary.subclient.instance_name,
        storage_policy=summary.storage_policy.name,
        percent_complete=summary.percent_complete,
        start_time=_from_epoch(summary.job_start_time),
        end_time=_from_epoch(summary.job_end_time),
        last_update_time=_from_epoch(summary.last_update_time),
        elapsed_seconds=summary.job_elapsed_time,
        size_of_application=summary.size_of_application,
        size_of_media=summary.size_of_media,
        total_files=summary.total_files,
        failed_files=summary.failed_files,
        pending_reason=summary.pending_reason or None,
        is_aged=summary.is_aged,
        raw=raw,
    )
