"""Rubrik in the API: its tables, in their own `rubrik` schema, and the
endpoints its collector talks to, under /rubrik/{account}.

The first product in a schema of its own, as docs/vision.md section 3.5
plans for every product: Commvault's tables move to a `commvault` schema
later. Nothing here touches them.

One row per job (RSC activity series) per RSC account, holding the
latest state the job was seen in - as `job` does for Commvault.
"""

# No `from __future__ import annotations`: see app.py - the routes are
# defined inside a function and name closure-local dependencies.

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import BigInteger, Column, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Field, Session, SQLModel, col, func, select

from blt.rubrik.schemas import (
    RubrikJobBatch,
    RubrikJobOut,
    RubrikLastRun,
    RubrikRunFinish,
    RubrikRunOut,
    RubrikRunStart,
)
from blt.schemas import UpsertResult

from .docs import UNAUTHORIZED, not_found

SCHEMA = "rubrik"

# Set on insert only: the key, and when the job was first seen.
_INSERT_ONLY = frozenset({"account_id", "series_key", "first_seen_at"})


def _ts(*, nullable: bool = True) -> Any:
    return Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=nullable))


class RubrikAccount(SQLModel, table=True):
    """One RSC account (https://<name>.my.rubrik.com), by the name blt
    knows it under - the name of its Prefect block."""

    __tablename__ = "account"
    __table_args__ = {"schema": SCHEMA}

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    created_at: datetime = _ts(nullable=False)


class RubrikRun(SQLModel, table=True):
    __tablename__ = "collection_run"
    __table_args__ = (
        Index("ix_rubrik_run_account_status_started", "account_id", "status", "started_at"),
        {"schema": SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key=f"{SCHEMA}.account.id")
    mode: str
    status: str
    started_at: datetime = _ts(nullable=False)
    finished_at: datetime | None = _ts()
    lookup_seconds: int = Field(sa_column=Column(BigInteger, nullable=False))
    jobs_collected: int = 0
    error: str | None = None


class RubrikJob(SQLModel, table=True):
    __tablename__ = "job"
    __table_args__ = (
        Index("ix_rubrik_job_account_active", "account_id", "is_active"),
        Index("ix_rubrik_job_account_updated", "account_id", "last_updated"),
        Index("ix_rubrik_job_account_start", "account_id", "start_time"),
        Index("ix_rubrik_job_account_type_updated", "account_id", "activity_type", "last_updated"),
        {"schema": SCHEMA},
    )

    account_id: int = Field(foreign_key=f"{SCHEMA}.account.id", primary_key=True)
    series_key: str = Field(primary_key=True)

    fid: str | None = None
    activity_series_id: str | None = None
    cluster_id: str | None = None
    cluster_name: str | None = None
    object_id: str | None = None
    object_name: str | None = Field(default=None, index=True)
    object_type: str | None = None
    location: str | None = None
    activity_type: str | None = None
    status: str | None = None
    is_active: bool = False
    severity: str | None = None
    progress: str | None = None
    percent_complete: float | None = None
    sla_domain_name: str | None = None
    last_activity_message: str | None = None
    failure_reason: str | None = None
    cause_error_code: str | None = None
    cause_error_message: str | None = None
    cause_error_reason: str | None = None
    cause_error_remedy: str | None = None
    attempt_number: int | None = None
    is_on_demand: bool | None = None
    data_transferred: int | None = Field(default=None, sa_column=Column(BigInteger))
    logical_size: int | None = Field(default=None, sa_column=Column(BigInteger))
    effective_throughput: int | None = Field(default=None, sa_column=Column(BigInteger))
    start_time: datetime | None = _ts()
    last_updated: datetime | None = _ts()
    raw: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))

    first_seen_at: datetime = _ts(nullable=False)
    last_collected_at: datetime = _ts(nullable=False)
    last_seen_run_id: int | None = Field(default=None, foreign_key=f"{SCHEMA}.collection_run.id")


def rubrik_router(
    get_session: Callable[[], Iterator[Session]], require_api_key: Callable[..., None]
) -> APIRouter:
    SessionDep = Annotated[Session, Depends(get_session)]
    router = APIRouter(
        prefix="/rubrik/{account_name}",
        tags=["rubrik"],
        dependencies=[Depends(require_api_key)],
        responses=UNAUTHORIZED,
    )

    def find_account(session: Session, name: str) -> RubrikAccount:
        account = session.exec(select(RubrikAccount).where(RubrikAccount.name == name)).first()
        if account is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown Rubrik account {name!r}")
        return account

    def get_or_create_account(session: Session, name: str) -> RubrikAccount:
        session.execute(
            pg_insert(RubrikAccount)
            .values(name=name, created_at=datetime.now(UTC))
            .on_conflict_do_nothing(index_elements=["name"])
        )
        return find_account(session, name)

    def find_run(session: Session, account: RubrikAccount, run_id: int) -> RubrikRun:
        run = session.get(RubrikRun, run_id)
        if run is None or run.account_id != account.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown run {run_id}")
        return run

    @router.get("/last-run", summary="Where the last collection got to")
    def last_run(account_name: str, session: SessionDep) -> RubrikLastRun:
        """The account's watermark and its most recent run. An unknown
        account is not an error: no watermark means do an initial run."""
        account = session.exec(
            select(RubrikAccount).where(RubrikAccount.name == account_name)
        ).first()
        if account is None:
            return RubrikLastRun(account=account_name)
        watermark = session.exec(
            select(func.max(RubrikRun.started_at)).where(
                RubrikRun.account_id == account.id, RubrikRun.status == "succeeded"
            )
        ).one()
        latest = session.exec(
            select(RubrikRun)
            .where(RubrikRun.account_id == account.id)
            .order_by(col(RubrikRun.started_at).desc())
            .limit(1)
        ).first()
        return RubrikLastRun(
            account=account_name,
            watermark=watermark,
            last_run=RubrikRunOut.model_validate(latest) if latest else None,
        )

    @router.post("/runs", status_code=status.HTTP_201_CREATED, summary="Open a collection run")
    def start_run(account_name: str, body: RubrikRunStart, session: SessionDep) -> RubrikRunOut:
        """Opens a run (status `running`); send its `id` with each batch.
        The account is created on its first run."""
        account = get_or_create_account(session, account_name)
        assert account.id is not None
        run = RubrikRun(
            account_id=account.id,
            mode=body.mode,
            status="running",
            started_at=body.started_at,
            lookup_seconds=body.lookup_seconds,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return RubrikRunOut.model_validate(run)

    @router.post(
        "/runs/{run_id}/finish",
        summary="Close a collection run",
        responses=not_found("account or run"),
    )
    def finish_run(
        account_name: str, run_id: int, body: RubrikRunFinish, session: SessionDep
    ) -> RubrikRunOut:
        """Records how the run went; a succeeded run becomes the new
        watermark."""
        run = find_run(session, find_account(session, account_name), run_id)
        run.status = body.status
        run.finished_at = body.finished_at
        run.jobs_collected = body.jobs_collected
        run.error = body.error
        session.add(run)
        session.commit()
        session.refresh(run)
        return RubrikRunOut.model_validate(run)

    @router.post("/jobs", summary="Store a batch of jobs")
    def upsert_jobs(account_name: str, body: RubrikJobBatch, session: SessionDep) -> UpsertResult:
        """Inserts new jobs and updates known ones, keyed by `series_key`.
        A job collected earlier than what is stored is left alone."""
        account = get_or_create_account(session, account_name)
        # One INSERT ... ON CONFLICT cannot touch a row twice: a series
        # repeated in a batch keeps its last occurrence.
        rows: dict[str, dict[str, object]] = {}
        for job in body.jobs:
            rows[job.series_key] = {
                **job.model_dump(),
                "account_id": account.id,
                "first_seen_at": body.collected_at,
                "last_collected_at": body.collected_at,
                "last_seen_run_id": body.run_id,
            }
        if not rows:
            session.commit()
            return UpsertResult(received=0, written=0)

        stmt = pg_insert(RubrikJob).values(list(rows.values()))
        stmt = stmt.on_conflict_do_update(
            index_elements=["account_id", "series_key"],
            set_={
                name: stmt.excluded[name]
                for name in next(iter(rows.values()))
                if name not in _INSERT_ONLY
            },
            # A slow run finishing after a newer one must not roll a job
            # back to an older state.
            where=col(RubrikJob.last_collected_at) <= stmt.excluded.last_collected_at,
        )
        written = len(session.execute(stmt.returning(col(RubrikJob.series_key))).all())
        session.commit()
        return UpsertResult(received=len(body.jobs), written=written)

    @router.get("/jobs", summary="List jobs", responses=not_found("account"))
    def list_jobs(
        account_name: str,
        session: SessionDep,
        active: Annotated[
            bool | None, Query(description="Only jobs still running (true) or finished (false).")
        ] = None,
        activity_type: Annotated[
            list[str] | None,
            Query(
                description="Only these activity types, any case; repeat for several: "
                "`?activity_type=Backup&activity_type=Replication`. blt collects every type, "
                "including RSC's own Index, Diagnostic, Classification and RadarAnalysis runs."
            ),
        ] = None,
        status_is: Annotated[
            str | None,
            Query(alias="status", description="RSC's status, any case: Success, Failure, ..."),
        ] = None,
        object_name: Annotated[
            str | None, Query(description="Part of the protected object's name, any case.")
        ] = None,
        updated_after: Annotated[
            datetime | None, Query(description="Only jobs RSC changed after this time.")
        ] = None,
        limit: Annotated[int, Query(ge=1, le=1000, description="Page size.")] = 100,
        offset: Annotated[int, Query(ge=0, description="Rows to skip.")] = 0,
    ) -> list[RubrikJobOut]:
        """Jobs, most recently updated first."""
        account = find_account(session, account_name)
        query = select(RubrikJob).where(RubrikJob.account_id == account.id)
        if active is not None:
            query = query.where(RubrikJob.is_active == active)
        if activity_type:
            wanted = [name.lower() for name in activity_type]
            query = query.where(func.lower(RubrikJob.activity_type).in_(wanted))
        if status_is is not None:
            query = query.where(func.lower(RubrikJob.status) == status_is.lower())
        if object_name:
            query = query.where(col(RubrikJob.object_name).ilike(f"%{object_name}%"))
        if updated_after is not None:
            query = query.where(col(RubrikJob.last_updated) > updated_after)
        query = (
            query.order_by(col(RubrikJob.last_updated).desc().nulls_last())
            .offset(offset)
            .limit(limit)
        )
        return [RubrikJobOut.model_validate(job) for job in session.exec(query).all()]

    return router
