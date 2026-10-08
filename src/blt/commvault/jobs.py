"""Jobs: listing them, looking one up, and (lab only) controlling them."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from pydantic import ValidationError
from sdk_primer import NotFoundError

from blt.schemas import JobIn

from ._base import Resource
from .models import job_in_from_summary

# POST /Jobs "category": 0 = all, 1 = active only, 2 = finished only.
CATEGORY_ALL = 0
CATEGORY_ACTIVE = 1
CATEGORY_FINISHED = 2

# pagingConfig "sortDirection": 0 = oldest first, 1 = newest first
# (checked against a live 11 SP46 CommServe). Oldest first is what makes
# offset paging safe: a job that starts mid-run lands after the last
# page instead of pushing every later job down by one.
_ASCENDING = 0


def parse_job(entry: dict[str, Any]) -> JobIn | None:
    """One entry of a job listing as a JobIn, or None if it can't be read.

    A CommServe holds years of jobs from many agents and versions, and
    one whose summary has a field in an unexpected shape should cost that
    one job, loudly, not the whole collection run."""
    summary = entry.get("jobSummary")
    if summary is None:
        return None
    try:
        return job_in_from_summary(summary)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        logger.error(
            "Skipping job {}: could not read its summary - {}", summary.get("jobId"), problems
        )
        return None


class Jobs(Resource):
    def _listing_body(
        self,
        lookup_seconds: int,
        category: int,
        offset: int,
        limit: int,
        extra_filter: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "scope": 1,
            "category": category,
            "pagingConfig": {
                "sortField": "jobId",
                "sortDirection": _ASCENDING,
                "offset": offset,
                "limit": limit,
            },
            "jobFilter": {
                "completedJobLookupTime": lookup_seconds,
                "showAgedJobs": True,
                "hideAdminJobs": False,
                "clientList": [],
                "jobTypeList": [],
                **(extra_filter or {}),
            },
        }

    def iter_pages(
        self,
        lookup_seconds: int,
        *,
        ended_between: tuple[datetime, datetime] | None = None,
    ) -> Iterator[list[JobIn]]:
        """Jobs Commvault will report, one page at a time (POST /Jobs).

        By default: all jobs currently active, plus every job that
        finished within the last `lookup_seconds` (aged jobs included).

        With `ended_between`, a slice of history instead: only finished
        jobs whose end time falls in that range, both ends inclusive.
        That is what lets old history be fetched a bounded piece at a
        time rather than in one query for everything.

        Paged oldest first. New jobs get higher ids, so ones started
        while this is running land after the last page instead of
        shifting earlier ones.
        """
        category = CATEGORY_ALL
        extra: dict[str, Any] = {}
        if ended_between is not None:
            # Without "finished only", every slice would also come back
            # with whatever happens to be active right now.
            category = CATEGORY_FINISHED
            extra["endTimeRange"] = {
                "fromTime": int(ended_between[0].timestamp()),
                "toTime": int(ended_between[1].timestamp()),
            }
        page_size = self._client.page_size
        offset = 0
        while True:
            body = self._query(
                "/Jobs", self._listing_body(lookup_seconds, category, offset, page_size, extra)
            )
            entries = body.get("jobs") or []
            jobs = [job for e in entries if (job := parse_job(e)) is not None]
            logger.info(
                "Jobs page offset={} returned {} of {} total",
                offset,
                len(entries),
                body.get("totalRecordsWithoutPaging"),
            )
            if jobs:
                yield jobs
            if len(entries) < page_size:
                return
            offset += page_size

    def active(self, limit: int = 500) -> list[JobIn]:
        """Jobs that are active right now (one page)."""
        body = self._query("/Jobs", self._listing_body(0, CATEGORY_ACTIVE, 0, limit))
        return [job for e in body.get("jobs") or [] if (job := parse_job(e)) is not None]

    def oldest_start(self, lookup_seconds: int) -> datetime | None:
        """When the oldest finished job the CommServe still has a record
        of started - the point a backfill has nothing left behind. One
        row, oldest first; None if there are no finished jobs at all."""
        body = self._query("/Jobs", self._listing_body(lookup_seconds, CATEGORY_FINISHED, 0, 1))
        for entry in body.get("jobs") or []:
            started = entry.get("jobSummary", {}).get("jobStartTime")
            if started:
                return datetime.fromtimestamp(int(started), tz=UTC)
        return None

    def get(self, job_id: int) -> JobIn | None:
        """One job's current summary (GET /Job/{id}), or None if the
        CommCell no longer knows the job at all."""
        try:
            body = self._get(f"/Job/{job_id}")
        except NotFoundError:
            return None
        for entry in body.get("jobs") or []:
            job = parse_job(entry)
            if job is not None:
                return job
        return None

    def details(self, job_id: int) -> dict[str, Any]:
        """Everything the CommServe reports about one job
        (POST /JobDetails): general, progress and detail info."""
        detail: dict[str, Any] = (
            self._query("/JobDetails", {"jobId": job_id}).get("job", {}).get("jobDetail", {})
        )
        return detail

    def counts(self, job_id: int) -> dict[str, Any]:
        """The CommServe's own totals for a job: how many objects it
        backed up and how many it skipped. For a SQL Server job an object
        is a database."""
        info = self.details(job_id).get("detailInfo", {})
        return {
            "job_id": job_id,
            "backed_up": info.get("numOfObjects"),
            "skipped": info.get("skippedItems"),
        }

    # -- changes: these alter running work on the CommServe ---------------

    def suspend(self, job_id: int) -> None:
        self._change("POST", f"/Job/{job_id}/action/pause")

    def resume(self, job_id: int) -> None:
        self._change("POST", f"/Job/{job_id}/action/resume")

    def kill(self, job_id: int) -> None:
        self._change("POST", f"/Job/{job_id}/action/kill")
