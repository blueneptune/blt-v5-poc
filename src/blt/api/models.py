"""Postgres tables.

`job` holds exactly one row per (CommCell, job id): the latest state that
job was seen in. Collections overwrite it rather than appending, so the
table is always "what does Commvault look like as of the last run" and
never a transition log.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _ts(*, nullable: bool = True, index: bool = False) -> Any:
    """A timezone-aware timestamp column. Every time in this schema is
    timestamptz - Commvault reports epoch seconds, so there is never a
    local time to preserve."""
    return Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=nullable, index=index)
    )


def _big() -> Any:
    return Field(default=None, sa_column=Column(BigInteger, nullable=True))


class Commcell(SQLModel, table=True):
    __tablename__ = "commcell"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    created_at: datetime = _ts(nullable=False)
    # Backfill position: history is complete back to here (see
    # blt.collector.collect.run_backfill). Null until a backfill has run.
    backfilled_to: datetime | None = _ts()
    backfill_complete: bool = Field(default=False, sa_column_kwargs={"server_default": "false"})


class CollectionRun(SQLModel, table=True):
    __tablename__ = "collection_run"
    __table_args__ = (
        Index("ix_run_commcell_status_started", "commcell_id", "status", "started_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    commcell_id: int = Field(foreign_key="commcell.id")
    mode: str
    status: str
    started_at: datetime = _ts(nullable=False)
    finished_at: datetime | None = _ts()
    lookup_seconds: int = Field(sa_column=Column(BigInteger, nullable=False))
    jobs_collected: int = 0
    jobs_reconciled: int = 0
    jobs_missing: int = 0
    error: str | None = None


class Job(SQLModel, table=True):
    __tablename__ = "job"
    __table_args__ = (
        Index("ix_job_commcell_active", "commcell_id", "is_active"),
        Index("ix_job_commcell_start", "commcell_id", "start_time"),
    )

    # Job ids are only unique inside one CommCell, hence the composite key.
    commcell_id: int = Field(foreign_key="commcell.id", primary_key=True)
    job_id: int = Field(sa_column=Column(BigInteger, primary_key=True, autoincrement=False))

    status: str
    state: str
    is_active: bool

    job_type: str | None = None
    operation: str | None = None
    backup_level: str | None = None
    app_type: str | None = None
    client_id: int | None = None
    client_name: str | None = Field(default=None, index=True)
    subclient_name: str | None = None
    backupset_name: str | None = None
    instance_name: str | None = None
    storage_policy: str | None = None
    percent_complete: float | None = None
    start_time: datetime | None = _ts()
    end_time: datetime | None = _ts()
    last_update_time: datetime | None = _ts()
    elapsed_seconds: int | None = _big()
    size_of_application: int | None = _big()
    size_of_media: int | None = _big()
    total_files: int | None = _big()
    failed_files: int | None = _big()
    pending_reason: str | None = None
    is_aged: bool | None = None
    raw: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))

    first_seen_at: datetime = _ts(nullable=False)
    last_collected_at: datetime = _ts(nullable=False)
    last_seen_run_id: int | None = Field(default=None, foreign_key="collection_run.id")


class SourceObject(SQLModel, table=True):
    """Something a source says exists: a client, an instance on it, a
    database in that. One row per thing per source, updated in place by
    each inventory run - the observation layer of docs/asset-refinery.md.

    Deliberately not named after Commvault or after backups: the same
    table is meant to take a ServiceNow CI or a vCenter VM later, each as
    its own source's row, to be linked rather than merged.
    """

    __tablename__ = "source_object"
    __table_args__ = (
        UniqueConstraint("commcell_id", "kind", "source_key", name="uq_source_object_key"),
        Index("ix_source_object_kind_present", "commcell_id", "kind", "present"),
    )

    # A UUID, so an object can be referred to from outside this database.
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    # The source. Still called commcell until sources are generalised
    # (TODO item 12); nothing else here is Commvault-specific.
    commcell_id: int = Field(foreign_key="commcell.id")
    kind: str
    source_key: str
    name: str = Field(index=True)
    parent_id: uuid.UUID | None = Field(default=None, foreign_key="source_object.id", index=True)
    app_type: str | None = None
    last_backup_at: datetime | None = _ts()
    last_backup_job_id: int | None = _big()
    last_full_job_id: int | None = _big()
    attributes: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )

    # False once an inventory run completes without having seen it.
    present: bool = True
    first_seen_at: datetime = _ts(nullable=False)
    last_seen_at: datetime = _ts(nullable=False)
    last_seen_run_id: int | None = Field(default=None, foreign_key="collection_run.id")


class InstanceBackupReport(SQLModel, table=True):
    """History of blt.schemas.InstanceReportRow: one row per instance per
    full backup job, written when an inventory run completes. The live
    report is always computed from `source_object`; this is what makes it
    possible to ask what the answer was for an earlier backup.

    `job_id` is 0 for "this instance has never had a backup", so that
    state is a row too (and unique per instance).
    """

    __tablename__ = "instance_backup_report"
    __table_args__ = (
        UniqueConstraint("commcell_id", "instance_id", "job_id", name="uq_instance_backup_report"),
    )

    id: int | None = Field(default=None, primary_key=True)
    commcell_id: int = Field(foreign_key="commcell.id")
    instance_id: uuid.UUID = Field(foreign_key="source_object.id", index=True)
    client: str | None = None
    instance: str
    app_type: str | None = None
    job_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    job_status: str | None = None
    job_ended_at: datetime | None = _ts()
    discovered: int
    backed_up: int
    reported_backed_up: int | None = None
    reported_skipped: int | None = None
    missed: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    unconfirmed: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    notes: str = ""
    observed_at: datetime = _ts(nullable=False)
