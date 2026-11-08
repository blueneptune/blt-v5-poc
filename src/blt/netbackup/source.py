"""Where the NetBackup job collector meets the NetBackup SDK: an SDK Job
becomes a NetBackupJobIn here and nowhere else."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from netbackup_sdk import Job, NetBackupClient

from .schemas import NetBackupJobIn


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def to_job_in(job: Job) -> NetBackupJobIn:
    raw = job.raw
    return NetBackupJobIn(
        job_id=job.job_id,
        parent_job_id=job.parent_job_id,
        job_type=job.job_type,
        state=job.state,
        status=job.status,
        is_active=job.is_active,
        policy_name=job.policy_name or None,
        policy_type=job.policy_type or None,
        schedule_name=job.schedule_name or None,
        schedule_type=job.schedule_type or None,
        client_name=job.client_name or None,
        media_server=job.media_server or None,
        storage_unit=job.storage_unit or None,
        # Not in the SDK's Job model; NetBackup's own names.
        source_media_server=_text(raw.get("sourceMediaServerName")),
        source_storage_unit=_text(raw.get("sourceStorageUnitName")),
        backup_id=job.backup_id or None,
        try_number=_int(raw.get("try")),
        start_time=job.start_time,
        end_time=job.end_time,
        last_update_time=job.last_update_time,
        kilobytes_transferred=job.kilobytes_transferred,
        kilobytes_data_transferred=job.kilobytes_data_transferred,
        kilobytes_to_transfer=_int(raw.get("kilobytesToTransfer")),
        number_of_files=job.number_of_files,
        percent_complete=job.percent_complete,
        raw=raw,
    )


class NetBackupJobs:
    """The collector's job source, backed by a NetBackupClient."""

    def __init__(self, nbu: NetBackupClient) -> None:
        self._jobs = nbu.jobs

    def iter_pages(self, updated_after: datetime) -> Iterator[list[NetBackupJobIn]]:
        for page in self._jobs.iter_pages(updated_after=updated_after):
            yield [to_job_in(job) for job in page]
