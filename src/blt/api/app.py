"""The ingest API: the only thing that reads or writes the job database.

Collectors never touch Postgres. They ask this service where they left
off (last-run), post what they collected (jobs), ask which jobs still
need checking on (jobs?active=true&unseen_in_run=...), and report how the
run went (runs/.../finish). That keeps the delta bookkeeping in one place
however many collectors, CommCells or hosts there end up being.

Run with:  uvicorn --factory blt.api.app:create_app
"""

# No `from __future__ import annotations` here: the routes are defined
# inside create_app() and name closure-local dependencies (SessionDep) in
# their signatures, which FastAPI can only resolve from real annotation
# objects, not from strings looked up in module globals.

import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, status
from sqlalchemy import Engine, create_engine, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, col, select

from blt.schemas import (
    JobBatch,
    JobOut,
    JobPatch,
    LastRun,
    RunFinish,
    RunOut,
    RunStart,
    UpsertResult,
    classify_status,
)

from .models import CollectionRun, Commcell, Job
from .settings import ApiSettings

# Set on insert only, never by an update: the key itself, and the moment a
# job was first seen.
_INSERT_ONLY = frozenset({"commcell_id", "job_id", "first_seen_at"})


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or ApiSettings()  # type: ignore[call-arg]
    engine: Engine = create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)

    def get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
        expected = settings.api_key.get_secret_value()
        if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid X-API-Key")

    SessionDep = Annotated[Session, Depends(get_session)]

    def find_commcell(session: Session, name: str) -> Commcell:
        commcell = session.exec(select(Commcell).where(Commcell.name == name)).first()
        if commcell is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown commcell {name!r}")
        return commcell

    def get_or_create_commcell(session: Session, name: str) -> Commcell:
        # ON CONFLICT DO NOTHING so two first-ever runs for the same
        # CommCell can't race each other into a unique-violation.
        session.execute(
            pg_insert(Commcell)
            .values(name=name, created_at=datetime.now(UTC))
            .on_conflict_do_nothing(index_elements=["name"])
        )
        return find_commcell(session, name)

    def find_run(session: Session, commcell: Commcell, run_id: int) -> CollectionRun:
        run = session.get(CollectionRun, run_id)
        if run is None or run.commcell_id != commcell.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown run {run_id}")
        return run

    app = FastAPI(title="blt job collection API", version="0.1.0")

    @app.get("/healthz")
    def healthz(session: SessionDep) -> dict[str, str]:
        session.execute(select(1))
        return {"status": "ok"}

    router = APIRouter(prefix="/commcells/{commcell_name}", dependencies=[Depends(require_api_key)])

    @router.get("/last-run")
    def last_run(commcell_name: str, session: SessionDep) -> LastRun:
        commcell = session.exec(select(Commcell).where(Commcell.name == commcell_name)).first()
        if commcell is None:
            # Never collected is a normal answer, not an error: it is how
            # a brand-new CommCell finds out it needs a full run.
            return LastRun(commcell=commcell_name)
        watermark = session.exec(
            select(func.max(CollectionRun.started_at)).where(
                CollectionRun.commcell_id == commcell.id, CollectionRun.status == "succeeded"
            )
        ).one()
        latest = session.exec(
            select(CollectionRun)
            .where(CollectionRun.commcell_id == commcell.id)
            .order_by(col(CollectionRun.started_at).desc())
            .limit(1)
        ).first()
        return LastRun(
            commcell=commcell_name,
            watermark=watermark,
            last_run=RunOut.model_validate(latest) if latest else None,
        )

    @router.post("/runs", status_code=status.HTTP_201_CREATED)
    def start_run(commcell_name: str, body: RunStart, session: SessionDep) -> RunOut:
        commcell = get_or_create_commcell(session, commcell_name)
        assert commcell.id is not None
        run = CollectionRun(
            commcell_id=commcell.id,
            mode=body.mode,
            status="running",
            started_at=body.started_at,
            lookup_seconds=body.lookup_seconds,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return RunOut.model_validate(run)

    @router.post("/runs/{run_id}/finish")
    def finish_run(commcell_name: str, run_id: int, body: RunFinish, session: SessionDep) -> RunOut:
        run = find_run(session, find_commcell(session, commcell_name), run_id)
        run.status = body.status
        run.finished_at = body.finished_at
        run.jobs_collected = body.jobs_collected
        run.jobs_reconciled = body.jobs_reconciled
        run.jobs_missing = body.jobs_missing
        run.error = body.error
        session.add(run)
        session.commit()
        session.refresh(run)
        return RunOut.model_validate(run)

    @router.post("/jobs")
    def upsert_jobs(commcell_name: str, body: JobBatch, session: SessionDep) -> UpsertResult:
        commcell = get_or_create_commcell(session, commcell_name)
        # Postgres refuses to touch the same row twice in one INSERT ...
        # ON CONFLICT, so a job repeated inside a batch keeps its last
        # occurrence.
        rows: dict[int, dict[str, object]] = {}
        for job in body.jobs:
            state, is_active = classify_status(job.status)
            rows[job.job_id] = {
                **job.model_dump(),
                "commcell_id": commcell.id,
                "state": state,
                "is_active": is_active,
                "first_seen_at": body.collected_at,
                "last_collected_at": body.collected_at,
                "last_seen_run_id": body.run_id,
            }
        if not rows:
            session.commit()
            return UpsertResult(received=0, written=0)

        stmt = pg_insert(Job).values(list(rows.values()))
        stmt = stmt.on_conflict_do_update(
            index_elements=["commcell_id", "job_id"],
            set_={
                name: stmt.excluded[name]
                for name in next(iter(rows.values()))
                if name not in _INSERT_ONLY
            },
            # A batch that was collected before what is already stored
            # (a slow run finishing after a newer one) must not roll a job
            # back to an older state.
            where=col(Job.last_collected_at) <= stmt.excluded.last_collected_at,
        )
        # RETURNING, not rowcount: a multi-row INSERT doesn't report one.
        written = len(session.execute(stmt.returning(col(Job.job_id))).all())
        session.commit()
        return UpsertResult(received=len(body.jobs), written=written)

    @router.get("/jobs")
    def list_jobs(
        commcell_name: str,
        session: SessionDep,
        active: bool | None = None,
        state: str | None = None,
        unseen_in_run: Annotated[
            int | None,
            Query(description="Only jobs that run did not collect - what is left to reconcile."),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[JobOut]:
        commcell = find_commcell(session, commcell_name)
        query = select(Job).where(Job.commcell_id == commcell.id)
        if active is not None:
            query = query.where(col(Job.is_active) == active)
        if state is not None:
            query = query.where(Job.state == state)
        if unseen_in_run is not None:
            query = query.where(
                col(Job.last_seen_run_id).is_(None) | (col(Job.last_seen_run_id) != unseen_in_run)
            )
        jobs = session.exec(query.order_by(col(Job.job_id)).limit(limit).offset(offset)).all()
        return [JobOut.model_validate(job) for job in jobs]

    def find_job(session: Session, commcell_name: str, job_id: int) -> Job:
        commcell = find_commcell(session, commcell_name)
        job = session.get(Job, (commcell.id, job_id))
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown job {job_id}")
        return job

    @router.get("/jobs/{job_id}")
    def get_job(commcell_name: str, job_id: int, session: SessionDep) -> JobOut:
        return JobOut.model_validate(find_job(session, commcell_name, job_id))

    @router.patch("/jobs/{job_id}")
    def patch_job(commcell_name: str, job_id: int, body: JobPatch, session: SessionDep) -> JobOut:
        job = find_job(session, commcell_name, job_id)
        job.status = body.status
        job.state, job.is_active = classify_status(body.status)
        job.last_collected_at = body.collected_at
        job.last_seen_run_id = body.run_id
        session.add(job)
        session.commit()
        session.refresh(job)
        return JobOut.model_validate(job)

    app.include_router(router)
    return app
