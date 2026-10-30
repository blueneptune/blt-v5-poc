"""Where the Rubrik collector meets the Rubrik SDK: an RSC activity
series becomes a RubrikJobIn, and an RSC object a RubrikObjectIn, here
and nowhere else."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from rubrik_sdk import ActivitySeries, InventoryObject, RubrikClient

from .schemas import RubrikJobIn, RubrikObjectIn


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
        percent_complete=series.percent_complete,
        sla_domain_name=series.sla_domain_name,
        last_activity_message=series.last_activity_message,
        failure_reason=series.failure_reason,
        cause_error_code=series.cause_error_code,
        cause_error_message=series.cause_error_message,
        cause_error_reason=series.cause_error_reason,
        cause_error_remedy=series.cause_error_remedy,
        attempt_number=series.attempt_number,
        is_on_demand=series.is_on_demand,
        data_transferred=series.data_transferred,
        logical_size=series.logical_size,
        effective_throughput=series.effective_throughput,
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


def to_object_in(obj: InventoryObject) -> RubrikObjectIn:
    return RubrikObjectIn(
        source_key=obj.key,
        fid=obj.fid,
        rsc_id=obj.id,
        name=obj.name,
        kind=obj.object_type,
        location=obj.location,
        object_state=obj.object_state,
        protection_status=obj.protection_status,
        compliance_status=obj.compliance_status,
        archival_compliance_status=obj.archival_compliance_status,
        replication_compliance_status=obj.replication_compliance_status,
        sla_domain_id=obj.sla_domain.id if obj.sla_domain else None,
        sla_domain_name=obj.sla_domain.name if obj.sla_domain else None,
        cluster_id=obj.cluster.id if obj.cluster else None,
        cluster_name=obj.cluster.name if obj.cluster else None,
        org_id=obj.org_id,
        protected_on=obj.protected_on,
        last_snapshot=obj.last_snapshot,
        latest_archival_snapshot=obj.latest_archival_snapshot,
        latest_replication_snapshot=obj.latest_replication_snapshot,
        awaiting_first_full=obj.awaiting_first_full,
        total_snapshots=obj.total_snapshots,
        missed_snapshots=obj.missed_snapshots,
        local_snapshots=obj.local_snapshots,
        archive_snapshots=obj.archive_snapshots,
        replica_snapshots=obj.replica_snapshots,
        logical_bytes=obj.logical_bytes,
        physical_bytes=obj.physical_bytes,
        local_storage=obj.local_storage,
        archive_storage=obj.archive_storage,
        replica_storage=obj.replica_storage,
        transferred_bytes=obj.transferred_bytes,
        pull_time=obj.pull_time,
    )


class RubrikInventory:
    """The inventory run's object source, backed by a RubrikClient."""

    def __init__(self, rsc: RubrikClient) -> None:
        self._inventory = rsc.inventory

    def iter_pages(self) -> Iterator[list[RubrikObjectIn]]:
        for page in self._inventory.iter_pages():
            yield [to_object_in(obj) for obj in page]
