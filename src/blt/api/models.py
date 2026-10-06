"""Postgres tables.

`job` holds exactly one row per (CommCell, job id): the latest state that
job was seen in. Collections overwrite it rather than appending, so the
table is always "what does Commvault look like as of the last run" and
never a transition log.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Index
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
