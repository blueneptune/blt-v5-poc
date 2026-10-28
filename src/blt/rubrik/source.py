"""Where the Rubrik collector meets the Rubrik SDK: an RSC activity
series becomes a RubrikJobIn here and nowhere else."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from rubrik_sdk import ActivitySeries, RubrikClient

from .schemas import RubrikJobIn


def to_job_in(series: ActivitySeries) -> RubrikJobIn:
    return RubrikJobIn(
        series_key=series.key,
        fid=series.fid,
        activity_series_id=series.activity_series_id,
        cluster_id=series.cluster.id if series.cluster else None,
        cluster_name=series.cluster.name if series.cluster else None,
        object_id=series.object_id,
        object_name=series.object_name,
        object_type=series.object_type,
        location=series.location,
        activity_type=series.last_activity_type,
        status=series.last_activity_status,
        is_active=series.is_active,
        severity=series.severity,
        progress=series.progress,
        sla_domain_name=series.sla_domain_name,
        start_time=series.start_time,
        last_updated=series.last_updated,
        raw=series.model_dump(mode="json", by_alias=True),
    )


class RubrikJobs:
    """The collector's job source, backed by a RubrikClient."""

    def __init__(self, rsc: RubrikClient) -> None:
        self._jobs = rsc.jobs

    def iter_pages(self, updated_after: datetime) -> Iterator[list[RubrikJobIn]]:
        for page in self._jobs.iter_pages(updated_after=updated_after):
            yield [to_job_in(series) for series in page]
