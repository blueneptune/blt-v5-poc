"""NetBackup in the API: its job tables, in their own `netbackup` schema,
and the endpoints its job collector talks to, under
/v1/sources/netbackup/{name} (beside storage, which is in common.storage).

One row per job per primary, holding the latest state the job was seen
in - as `job` does for Commvault and Rubrik. NetBackup forgets finished
jobs after a few days; blt does not.
"""

# No `from __future__ import annotations`: see app.py - the routes are
# defined inside a function and name closure-local dependencies.

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import BigInteger, Column, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Field, Session, SQLModel, col, func, select

from blt.common.mapping import netbackup_job
from blt.netbackup.schemas import (
    NetBackupJobBatch,
    NetBackupJobOut,
    NetBackupLastRun,
    NetBackupRunFinish,
    NetBackupRunOut,
    NetBackupRunStart,
    NetBackupSlpBatch,
    NetBackupSlpOut,
)
from blt.schemas import UpsertResult

from . import jobs as common_jobs
from .docs import UNAUTHORIZED, NetBackupPrimaryName, RunId, not_found
from .sources import SCHEMA as BLT_SCHEMA
from .sources import ensure_source

SCHEMA = "netbackup"

# Set on insert only: the key, and when the job was first seen.
_INSERT_ONLY = frozenset({"primary_id", "job_id", "first_seen_at"})


def _ts(*, nullable: bool = True) -> Any:
    return Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=nullable))


def _big() -> Any:
    return Field(default=None, sa_column=Column(BigInteger, nullable=True))


class NetBackupPrimary(SQLModel, table=True):
    """One NetBackup primary server, by the name blt knows it under - the
    name of its Prefect block."""

    __tablename__ = "primary_server"
    __table_args__ = {"schema": SCHEMA}

    id: int | None = Field(default=None, primary_key=True)
    # The primary's row in common.source: its id outside this schema.
    source_id: uuid.UUID = Field(foreign_key=f"{BLT_SCHEMA}.source.id", unique=True)
    name: str = Field(index=True, unique=True)
    created_at: datetime = _ts(nullable=False)


class NetBackupRun(SQLModel, table=True):
    __tablename__ = "collection_run"
    __table_args__ = (
        Index("ix_netbackup_run_primary_status_started", "primary_id", "status", "started_at"),
        {"schema": SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    primary_id: int = Field(foreign_key=f"{SCHEMA}.primary_server.id")
    mode: str
    status: str
    started_at: datetime = _ts(nullable=False)
    finished_at: datetime | None = _ts()
    lookup_seconds: int = Field(sa_column=Column(BigInteger, nullable=False))
    jobs_collected: int = 0
    error: str | None = None


class NetBackupJob(SQLModel, table=True):
    __tablename__ = "job"
    __table_args__ = (
        Index("ix_netbackup_job_primary_active", "primary_id", "is_active"),
        Index("ix_netbackup_job_primary_updated", "primary_id", "last_update_time"),
        Index("ix_netbackup_job_primary_start", "primary_id", "start_time"),
        Index(
            "ix_netbackup_job_primary_type_updated", "primary_id", "job_type", "last_update_time"
        ),
        Index("ix_netbackup_job_backup_id", "backup_id"),
        {"schema": SCHEMA},
    )

    primary_id: int = Field(foreign_key=f"{SCHEMA}.primary_server.id", primary_key=True)
    job_id: int = Field(
        sa_column=Column(BigInteger, primary_key=True, autoincrement=False, nullable=False)
    )

    parent_job_id: int | None = _big()
    job_type: str | None = None
    state: str = ""
    status: int | None = None
    is_active: bool = False
    policy_name: str | None = None
    policy_type: str | None = None
    schedule_name: str | None = None
    schedule_type: str | None = None
    client_name: str | None = Field(default=None, index=True)
    media_server: str | None = None
    storage_unit: str | None = None
    source_media_server: str | None = None
    source_storage_unit: str | None = None
    backup_id: str | None = None
    try_number: int | None = None
    start_time: datetime | None = _ts()
    end_time: datetime | None = _ts()
    last_update_time: datetime | None = _ts()
    kilobytes_transferred: int | None = _big()
    kilobytes_data_transferred: int | None = _big()
    kilobytes_to_transfer: int | None = _big()
    number_of_files: int | None = _big()
    percent_complete: int | None = None
    raw: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))

    first_seen_at: datetime = _ts(nullable=False)
    last_collected_at: datetime = _ts(nullable=False)
    last_seen_run_id: int | None = Field(default=None, foreign_key=f"{SCHEMA}.collection_run.id")


class NetBackupSlp(SQLModel, table=True):
    """A storage lifecycle policy (SLP) on a primary, as last collected:
    which storage each step writes to and which step it copies from - so
    a copy job, which names its SLP as its policy, can be traced back to
    the storage it read (blt.api.storage_flows)."""

    __tablename__ = "slp"
    __table_args__ = {"schema": SCHEMA}

    primary_id: int = Field(foreign_key=f"{SCHEMA}.primary_server.id", primary_key=True)
    name: str = Field(primary_key=True)
    version: int | None = None
    active: bool | None = None
    operations: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    collected_at: datetime = _ts(nullable=False)


def netbackup_router(
    get_session: Callable[[], Iterator[Session]], require_api_key: Callable[..., None]
) -> APIRouter:
    SessionDep = Annotated[Session, Depends(get_session)]
    # No prefix: app.py mounts it under /v1/sources/netbackup/{source_name}.
    router = APIRouter(
        tags=["netbackup"],
        dependencies=[Depends(require_api_key)],
        responses=UNAUTHORIZED,
    )

    def find_primary(session: Session, name: str) -> NetBackupPrimary:
        primary = session.exec(
            select(NetBackupPrimary).where(NetBackupPrimary.name == name)
        ).first()
        if primary is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown NetBackup primary {name!r}")
        return primary

    def get_or_create_primary(session: Session, name: str) -> NetBackupPrimary:
        session.execute(
            pg_insert(NetBackupPrimary)
            .values(
                name=name,
                source_id=ensure_source(session, "netbackup", name),
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(index_elements=["name"])
        )
        return find_primary(session, name)

    def find_run(session: Session, primary: NetBackupPrimary, run_id: int) -> NetBackupRun:
        run = session.get(NetBackupRun, run_id)
        if run is None or run.primary_id != primary.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown run {run_id}")
        return run

    @router.get("/last-run", summary="Where the last job collection got to")
    def last_run(source_name: NetBackupPrimaryName, session: SessionDep) -> NetBackupLastRun:
        """The primary's watermark and its most recent job run. An unknown
        primary is not an error: no watermark means do an initial run."""
        primary = session.exec(
            select(NetBackupPrimary).where(NetBackupPrimary.name == source_name)
        ).first()
        if primary is None:
            return NetBackupLastRun(primary=source_name)
        watermark = session.exec(
            select(func.max(NetBackupRun.started_at)).where(
                NetBackupRun.primary_id == primary.id, NetBackupRun.status == "succeeded"
            )
        ).one()
        latest = session.exec(
            select(NetBackupRun)
            .where(NetBackupRun.primary_id == primary.id)
            .order_by(col(NetBackupRun.started_at).desc())
            .limit(1)
        ).first()
        return NetBackupLastRun(
            primary=source_name,
            watermark=watermark,
            last_run=NetBackupRunOut.model_validate(latest) if latest else None,
        )

    @router.post("/runs", status_code=status.HTTP_201_CREATED, summary="Open a job collection run")
    def start_run(
        source_name: NetBackupPrimaryName, body: NetBackupRunStart, session: SessionDep
    ) -> NetBackupRunOut:
        """Opens a run (status `running`); send its `id` with each batch.
        The primary is created on its first run."""
        primary = get_or_create_primary(session, source_name)
        assert primary.id is not None
        run = NetBackupRun(
            primary_id=primary.id,
            mode=body.mode,
            status="running",
            started_at=body.started_at,
            lookup_seconds=body.lookup_seconds,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return NetBackupRunOut.model_validate(run)

    @router.post(
        "/runs/{run_id}/finish",
        summary="Close a job collection run",
        responses=not_found("primary or run"),
    )
    def finish_run(
        source_name: NetBackupPrimaryName,
        run_id: RunId,
        body: NetBackupRunFinish,
        session: SessionDep,
    ) -> NetBackupRunOut:
        """Records how the run went. A succeeded run becomes the new
        watermark."""
        primary = find_primary(session, source_name)
        run = find_run(session, primary, run_id)
        run.status = body.status
        run.finished_at = body.finished_at
        run.jobs_collected = body.jobs_collected
        run.error = body.error
        session.add(run)
        session.commit()
        session.refresh(run)
        return NetBackupRunOut.model_validate(run)

    @router.post("/jobs", summary="Store a batch of jobs")
    def upsert_jobs(
        source_name: NetBackupPrimaryName, body: NetBackupJobBatch, session: SessionDep
    ) -> UpsertResult:
        """Inserts new jobs and updates known ones, keyed by `job_id`. A job
        collected earlier than what is stored is left alone."""
        primary = get_or_create_primary(session, source_name)
        # One INSERT ... ON CONFLICT cannot touch a row twice: a job
        # repeated in a batch keeps its last occurrence.
        rows: dict[int, dict[str, object]] = {}
        for job in body.jobs:
            rows[job.job_id] = {
                **job.model_dump(),
                "primary_id": primary.id,
                "first_seen_at": body.collected_at,
                "last_collected_at": body.collected_at,
                "last_seen_run_id": body.run_id,
            }
        if not rows:
            session.commit()
            return UpsertResult(received=0, written=0)

        stmt = pg_insert(NetBackupJob).values(list(rows.values()))
        stmt = stmt.on_conflict_do_update(
            index_elements=["primary_id", "job_id"],
            set_={
                name: stmt.excluded[name]
                for name in next(iter(rows.values()))
                if name not in _INSERT_ONLY
            },
            # A slow run finishing after a newer one must not roll a job
            # back to an older state.
            where=col(NetBackupJob.last_collected_at) <= stmt.excluded.last_collected_at,
        )
        written = [r[0] for r in session.execute(stmt.returning(col(NetBackupJob.job_id)))]
        common_jobs.project(session, primary.source_id, [rows[k] for k in written], netbackup_job)
        session.commit()
        return UpsertResult(received=len(body.jobs), written=len(written))

    @router.post("/slps", summary="Store every SLP of the primary")
    def replace_slps(
        source_name: NetBackupPrimaryName, body: NetBackupSlpBatch, session: SessionDep
    ) -> UpsertResult:
        """The primary's storage lifecycle policies, all of them: each is
        stored as sent, and SLPs not in the batch are removed. A batch
        older than what is stored is left alone."""
        primary = get_or_create_primary(session, source_name)
        newest = session.exec(
            select(func.max(NetBackupSlp.collected_at)).where(NetBackupSlp.primary_id == primary.id)
        ).one()
        if newest is not None and newest > body.collected_at:
            session.commit()
            return UpsertResult(received=len(body.slps), written=0)
        for old in session.exec(select(NetBackupSlp).where(NetBackupSlp.primary_id == primary.id)):
            session.delete(old)
        session.flush()
        latest = {slp.name: slp for slp in body.slps}
        for slp in latest.values():
            session.add(
                NetBackupSlp(
                    primary_id=primary.id,  # type: ignore[arg-type]
                    name=slp.name,
                    version=slp.version,
                    active=slp.active,
                    operations=[o.model_dump() for o in slp.operations],
                    collected_at=body.collected_at,
                )
            )
        session.commit()
        return UpsertResult(received=len(body.slps), written=len(latest))

    @router.get("/slps", summary="List the primary's SLPs", responses=not_found("primary"))
    def list_slps(source_name: NetBackupPrimaryName, session: SessionDep) -> list[NetBackupSlpOut]:
        """Its storage lifecycle policies as last collected, by name."""
        primary = find_primary(session, source_name)
        rows = session.exec(
            select(NetBackupSlp)
            .where(NetBackupSlp.primary_id == primary.id)
            .order_by(col(NetBackupSlp.name))
        )
        return [NetBackupSlpOut.model_validate(r, from_attributes=True) for r in rows]

    @router.get("/jobs", summary="List jobs", responses=not_found("primary"))
    def list_jobs(
        source_name: NetBackupPrimaryName,
        session: SessionDep,
        active: Annotated[
            bool | None, Query(description="Only jobs still going (true) or done (false).")
        ] = None,
        job_type: Annotated[
            list[str] | None,
            Query(
                description="Only these job types, any case; repeat for several: "
                "`?job_type=BACKUP&job_type=DUPLICATE`."
            ),
        ] = None,
        status_code: Annotated[
            int | None, Query(alias="status", description="NetBackup's status code: 0, 1, 58, ...")
        ] = None,
        client_name: Annotated[
            str | None, Query(description="Part of the client's name, any case.")
        ] = None,
        policy_name: Annotated[str | None, Query(description="The policy, exactly.")] = None,
        backup_id: Annotated[str | None, Query(description="The image's backup id.")] = None,
        updated_after: Annotated[
            datetime | None, Query(description="Only jobs NetBackup changed after this time.")
        ] = None,
        limit: Annotated[int, Query(ge=1, le=1000, description="Page size.")] = 100,
        offset: Annotated[int, Query(ge=0, description="Rows to skip.")] = 0,
    ) -> list[NetBackupJobOut]:
        """Jobs, most recently updated first."""
        primary = find_primary(session, source_name)
        query = select(NetBackupJob).where(NetBackupJob.primary_id == primary.id)
        if active is not None:
            query = query.where(NetBackupJob.is_active == active)
        if job_type:
            query = query.where(
                func.upper(NetBackupJob.job_type).in_([t.upper() for t in job_type])
            )
        if status_code is not None:
            query = query.where(NetBackupJob.status == status_code)
        if client_name:
            query = query.where(col(NetBackupJob.client_name).ilike(f"%{client_name}%"))
        if policy_name:
            query = query.where(NetBackupJob.policy_name == policy_name)
        if backup_id:
            query = query.where(NetBackupJob.backup_id == backup_id)
        if updated_after is not None:
            query = query.where(col(NetBackupJob.last_update_time) > updated_after)
        query = (
            query.order_by(col(NetBackupJob.last_update_time).desc().nulls_last())
            .offset(offset)
            .limit(limit)
        )
        return [NetBackupJobOut.model_validate(job) for job in session.exec(query).all()]

    @router.get("/jobs/{job_id}", summary="One job", responses=not_found("primary or job"))
    def get_job(
        source_name: NetBackupPrimaryName,
        job_id: Annotated[int, Path(description="NetBackup's job id.", examples=[48211])],
        session: SessionDep,
    ) -> NetBackupJobOut:
        """One job by its id, with NetBackup's full record in `raw`."""
        primary = find_primary(session, source_name)
        job = session.get(NetBackupJob, (primary.id, job_id))
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown job {job_id}")
        return NetBackupJobOut.model_validate(job)

    return router
