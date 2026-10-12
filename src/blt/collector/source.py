"""Where the collector meets the Commvault SDK.

The SDK (the separate commvault-sdk package) returns its own `Job`.
blt's wire model is `JobIn`, defined in blt.schemas so the API can use it
without the SDK installed. They have the same fields on purpose; this is
the one place a Job becomes a JobIn.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from commvault_sdk import CommvaultClient, Job

from blt.schemas import JobIn


def to_job_in(job: Job) -> JobIn:
    return JobIn.model_validate(job.model_dump())


class CommvaultJobs:
    """The collector's JobSource, backed by a CommvaultClient."""

    def __init__(self, commvault: CommvaultClient) -> None:
        self._jobs = commvault.jobs

    def iter_pages(
        self,
        lookup_seconds: int,
        *,
        ended_between: tuple[datetime, datetime] | None = None,
    ) -> Iterator[list[JobIn]]:
        for page in self._jobs.iter_pages(lookup_seconds, ended_between=ended_between):
            yield [to_job_in(job) for job in page]

    def get(self, job_id: int) -> JobIn | None:
        job = self._jobs.get(job_id)
        return to_job_in(job) if job is not None else None

    def oldest_start(self, lookup_seconds: int) -> datetime | None:
        return self._jobs.oldest_start(lookup_seconds)
