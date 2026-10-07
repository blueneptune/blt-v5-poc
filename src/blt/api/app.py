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
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, status
from sqlalchemy import Engine, create_engine, func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, col, select

from blt.schemas import (
    PROBLEM_VERDICTS,
    BackfillState,
    BackfillUpdate,
    InstanceReportRow,
    JobBatch,
    JobOut,
    JobPatch,
    LastRun,
    ObjectBatch,
    ObjectOut,
    RunFinish,
    RunOut,
    RunStart,
    UpsertResult,
    ValidationReport,
    ValidationRow,
    classify_status,
)

from .models import CollectionRun, Commcell, InstanceBackupReport, Job, SourceObject
from .settings import ApiSettings

# Runs that collect "now": the ones a watermark can be taken from. A
# backfill run started at noon says nothing about what happened up to
# noon, so it must never move the watermark.
_COLLECTION_MODES = ("initial", "delta", "full")

# Set on insert only, never by an update: the key itself, and the moment a
# job was first seen.
_INSERT_ONLY = frozenset({"commcell_id", "job_id", "first_seen_at"})

# SQL Server databases every instance has. The backup product protects
# them but does not report a backup time for them the way it does for
# user databases, so they can be confirmed present, not confirmed fresh.
_SQL_SYSTEM_DATABASES = frozenset({"master", "model", "msdb"})


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
                CollectionRun.commcell_id == commcell.id,
                CollectionRun.status == "succeeded",
                col(CollectionRun.mode).in_(_COLLECTION_MODES),
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

    @router.get("/backfill")
    def backfill_state(commcell_name: str, session: SessionDep) -> BackfillState:
        commcell = session.exec(select(Commcell).where(Commcell.name == commcell_name)).first()
        if commcell is None:
            return BackfillState(commcell=commcell_name)
        resume_from = commcell.backfilled_to
        if resume_from is None:
            # No backfill yet: start behind what ordinary runs have
            # already reached back to (each covered its start time minus
            # its lookup window).
            runs = session.exec(
                select(CollectionRun.started_at, CollectionRun.lookup_seconds).where(
                    CollectionRun.commcell_id == commcell.id,
                    CollectionRun.status == "succeeded",
                    col(CollectionRun.mode).in_(_COLLECTION_MODES),
                )
            ).all()
            reached = [started - timedelta(seconds=lookup) for started, lookup in runs]
            resume_from = min(reached) if reached else None
        return BackfillState(
            commcell=commcell_name,
            resume_from=resume_from,
            backfilled_to=commcell.backfilled_to,
            complete=commcell.backfill_complete,
        )

    @router.put("/backfill")
    def save_backfill(
        commcell_name: str, body: BackfillUpdate, session: SessionDep
    ) -> BackfillState:
        commcell = find_commcell(session, commcell_name)
        commcell.backfilled_to = body.backfilled_to
        commcell.backfill_complete = body.complete
        session.add(commcell)
        session.commit()
        return backfill_state(commcell_name, session)

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
        if run.mode == "inventory" and body.status == "succeeded":
            # A complete inventory is a statement about absence too:
            # whatever it did not list is no longer there. Only a run
            # that finished may say so - a partial one saw less because
            # it stopped, not because things vanished.
            session.execute(
                update(SourceObject)
                .where(
                    col(SourceObject.commcell_id) == run.commcell_id,
                    col(SourceObject.present).is_(True),
                    col(SourceObject.last_seen_run_id).is_distinct_from(run.id),
                )
                .values(present=False)
            )
            # Keep what the per-instance report says as of this
            # inventory, one row per instance per full backup job.
            observed = body.finished_at
            for instance_id, row in build_instance_report(session, run.commcell_id):
                values = {
                    **row.model_dump(exclude={"observed_at"}),
                    "commcell_id": run.commcell_id,
                    "instance_id": instance_id,
                    "job_id": row.job_id or 0,
                    "observed_at": observed,
                }
                stmt = pg_insert(InstanceBackupReport).values(values)
                session.execute(
                    stmt.on_conflict_do_update(
                        constraint="uq_instance_backup_report",
                        set_={
                            k: stmt.excluded[k]
                            for k in values
                            if k not in {"commcell_id", "instance_id", "job_id"}
                        },
                    )
                )
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

    @router.post("/objects")
    def upsert_objects(commcell_name: str, body: ObjectBatch, session: SessionDep) -> UpsertResult:
        commcell = find_commcell(session, commcell_name)
        find_run(session, commcell, body.run_id)
        if not body.objects:
            return UpsertResult(received=0, written=0)

        # Parents are named by (kind, key); turn those into ids.
        wanted = {(o.parent_kind, o.parent_key) for o in body.objects if o.parent_key}
        parents: dict[tuple[str | None, str | None], object] = {}
        if wanted:
            known = session.exec(
                select(SourceObject.kind, SourceObject.source_key, SourceObject.id).where(
                    SourceObject.commcell_id == commcell.id,
                    col(SourceObject.source_key).in_({key for _, key in wanted}),
                )
            ).all()
            parents = {(kind, key): object_id for kind, key, object_id in known}
        missing = wanted - set(parents)
        if missing:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Unknown parent(s) {sorted(str(m) for m in missing)} - send parents first",
            )

        rows = {}
        for item in body.objects:
            rows[(item.kind, item.source_key)] = {
                **item.model_dump(exclude={"parent_kind", "parent_key"}),
                "commcell_id": commcell.id,
                "parent_id": parents.get((item.parent_kind, item.parent_key)),
                "present": True,
                "first_seen_at": body.collected_at,
                "last_seen_at": body.collected_at,
                "last_seen_run_id": body.run_id,
            }
        stmt = pg_insert(SourceObject).values(list(rows.values()))
        stmt = stmt.on_conflict_do_update(
            constraint="uq_source_object_key",
            set_={
                name: stmt.excluded[name]
                for name in next(iter(rows.values()))
                if name not in {"commcell_id", "kind", "source_key", "first_seen_at"}
            },
        )
        written = len(session.execute(stmt.returning(col(SourceObject.id))).all())
        session.commit()
        return UpsertResult(received=len(body.objects), written=written)

    def object_out(item: SourceObject) -> ObjectOut:
        return ObjectOut(
            id=str(item.id),
            kind=item.kind,  # type: ignore[arg-type]
            source_key=item.source_key,
            name=item.name,
            parent_id=str(item.parent_id) if item.parent_id else None,
            app_type=item.app_type,
            last_backup_at=item.last_backup_at,
            last_backup_job_id=item.last_backup_job_id,
            present=item.present,
            first_seen_at=item.first_seen_at,
            last_seen_at=item.last_seen_at,
            attributes=item.attributes,
        )

    @router.get("/objects")
    def list_objects(
        commcell_name: str,
        session: SessionDep,
        kind: str | None = None,
        present: bool | None = None,
        limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[ObjectOut]:
        commcell = find_commcell(session, commcell_name)
        query = select(SourceObject).where(SourceObject.commcell_id == commcell.id)
        if kind is not None:
            query = query.where(SourceObject.kind == kind)
        if present is not None:
            query = query.where(col(SourceObject.present) == present)
        query = query.order_by(col(SourceObject.kind), col(SourceObject.name))
        return [object_out(o) for o in session.exec(query.limit(limit).offset(offset)).all()]

    def build_instance_report(
        session: Session, commcell_id: int | None
    ) -> list[tuple[uuid.UUID, InstanceReportRow]]:
        """The per-instance report, from the current inventory: for each
        SQL Server instance, its most recent full backup measured against
        the databases known on it."""
        objects = session.exec(
            select(SourceObject).where(
                SourceObject.commcell_id == commcell_id, col(SourceObject.present).is_(True)
            )
        ).all()
        by_id = {o.id: o for o in objects}
        children: dict[object, list[SourceObject]] = {}
        for item in objects:
            if item.kind == "database" and item.parent_id:
                children.setdefault(item.parent_id, []).append(item)
        full_jobs = {
            d.last_full_job_id for ds in children.values() for d in ds if d.last_full_job_id
        }
        jobs = {
            job.job_id: job
            for job in session.exec(
                select(Job).where(Job.commcell_id == commcell_id, col(Job.job_id).in_(full_jobs))
            ).all()
        }

        result = []
        for instance in objects:
            if instance.kind != "instance" or instance.app_type != "SQL Server":
                continue
            client = by_id.get(instance.parent_id) if instance.parent_id else None
            databases = sorted(children.get(instance.id, []), key=lambda d: d.name)
            base = {
                "client": client.name if client else None,
                "instance": instance.name,
                "app_type": instance.app_type,
                "discovered": len(databases),
            }
            # The instance's latest full is the newest one any of its
            # databases was last fully backed up by.
            job_id = max((d.last_full_job_id or 0 for d in databases), default=0)
            if not job_id:
                note = (
                    "no databases discovered - no SQL backup has ever run here"
                    if not databases
                    else "databases are known but none has a full backup on record"
                )
                missed = [d.name for d in databases if d.name.lower() not in _SQL_SYSTEM_DATABASES]
                result.append(
                    (instance.id, InstanceReportRow(**base, backed_up=0, missed=missed, notes=note))
                )
                continue

            user = [d for d in databases if d.name.lower() not in _SQL_SYSTEM_DATABASES]
            system = [d.name for d in databases if d.name.lower() in _SQL_SYSTEM_DATABASES]
            taken = [d.name for d in user if d.last_full_job_id == job_id]
            missed = [d.name for d in user if d.last_full_job_id != job_id]
            reported = (instance.attributes or {}).get("last_full_job") or {}
            reported_count = reported.get("backed_up") if reported.get("job_id") == job_id else None
            reported_skipped = reported.get("skipped") if reported.get("job_id") == job_id else None

            # System databases have no per-database job to check. If the
            # job's own total accounts for them, count them as taken.
            unconfirmed = system
            if reported_count is not None and reported_count >= len(taken) + len(system):
                backed_up, unconfirmed = len(taken) + len(system), []
            else:
                backed_up = len(taken)

            notes = []
            if missed:
                notes.append("missed: " + ", ".join(missed))
            if unconfirmed:
                notes.append("not confirmed (system): " + ", ".join(unconfirmed))
            if reported_skipped:
                notes.append(f"job reports {reported_skipped} skipped")
            job = jobs.get(job_id)
            if job is None:
                notes.append("job not collected by blt yet")
            elif job.state != "completed":
                notes.append(f"job ended {job.status}")
            result.append(
                (
                    instance.id,
                    InstanceReportRow(
                        **base,
                        job_id=job_id,
                        job_status=job.status if job else None,
                        job_ended_at=job.end_time if job else None,
                        backed_up=backed_up,
                        reported_backed_up=reported_count,
                        reported_skipped=reported_skipped,
                        missed=missed,
                        unconfirmed=unconfirmed,
                        notes="; ".join(notes) or "all discovered databases backed up",
                    ),
                )
            )
        result.sort(key=lambda pair: (pair[1].client or "", pair[1].instance))
        return result

    @router.get("/instance-report")
    def instance_report(
        commcell_name: str,
        session: SessionDep,
        history: Annotated[
            bool, Query(description="Every stored row, one per instance per full backup job.")
        ] = False,
    ) -> list[InstanceReportRow]:
        """Databases discovered against databases backed up, per SQL
        Server instance, for its most recent full backup."""
        commcell = find_commcell(session, commcell_name)
        if history:
            stored = session.exec(
                select(InstanceBackupReport)
                .where(InstanceBackupReport.commcell_id == commcell.id)
                .order_by(
                    col(InstanceBackupReport.client),
                    col(InstanceBackupReport.instance),
                    col(InstanceBackupReport.job_id),
                )
            ).all()
            return [
                InstanceReportRow.model_validate(row).model_copy(
                    update={"job_id": row.job_id or None}
                )
                for row in stored
            ]
        return [row for _, row in build_instance_report(session, commcell.id)]

    @router.get("/validation")
    def validation(
        commcell_name: str,
        session: SessionDep,
        max_age_hours: Annotated[float, Query(gt=0)] = 24,
    ) -> ValidationReport:
        """Is everything this source says exists also backed up?

        Judged entirely from the source's own inventory (see
        ValidationReport for what that can and cannot establish), with
        each database's last backup job looked up in the job table as a
        second opinion on whether that backup actually succeeded.
        """
        commcell = find_commcell(session, commcell_name)
        objects = session.exec(
            select(SourceObject).where(SourceObject.commcell_id == commcell.id)
        ).all()
        by_id = {o.id: o for o in objects}
        inventory_at = session.exec(
            select(func.max(CollectionRun.started_at)).where(
                CollectionRun.commcell_id == commcell.id,
                CollectionRun.mode == "inventory",
                CollectionRun.status == "succeeded",
            )
        ).one()
        job_ids = {o.last_backup_job_id for o in objects if o.last_backup_job_id}
        job_status = dict(
            session.exec(
                select(Job.job_id, Job.status).where(
                    Job.commcell_id == commcell.id, col(Job.job_id).in_(job_ids)
                )
            ).all()
        )
        now = datetime.now(UTC)
        limit = timedelta(hours=max_age_hours)

        def names(item: SourceObject) -> tuple[str | None, str | None]:
            """(client, instance) above an object."""
            client = instance = None
            parent = by_id.get(item.parent_id) if item.parent_id else None
            while parent is not None:
                if parent.kind == "instance":
                    instance = parent.name
                elif parent.kind == "client":
                    client = parent.name
                parent = by_id.get(parent.parent_id) if parent.parent_id else None
            return client, instance

        rows: list[ValidationRow] = []
        with_databases = {
            o.parent_id for o in objects if o.kind == "database" and o.present and o.parent_id
        }
        for item in objects:
            client, instance = names(item)
            if item.kind == "instance":
                # Only database instances are expected to have databases.
                if not item.present or item.app_type != "SQL Server" or item.id in with_databases:
                    continue
                rows.append(
                    ValidationRow(
                        client=client,
                        kind="instance",
                        name=item.name,
                        app_type=item.app_type,
                        verdict="empty",
                        detail="no databases known on this instance - never backed up, "
                        "or the agent cannot log in to it",
                    )
                )
                continue
            if item.kind != "database":
                continue
            last = item.last_backup_at
            if not item.present:
                verdict, detail = "gone", f"last listed {item.last_seen_at:%Y-%m-%d %H:%M}Z"
            elif last is None and item.name.lower() in _SQL_SYSTEM_DATABASES:
                verdict = "unverified"
                detail = "system database: in backup content, no backup time reported for it"
            elif last is None:
                verdict, detail = "unprotected", "known to the source, no backup of it"
            elif now - last > limit:
                hours = (now - last).total_seconds() / 3600
                verdict = "stale"
                detail = f"last backup {hours:.1f} h ago (limit {max_age_hours:g} h)"
            else:
                verdict, detail = "ok", f"last backup {last:%Y-%m-%d %H:%M}Z"
            rows.append(
                ValidationRow(
                    client=client,
                    instance=instance,
                    kind="database",
                    name=item.name,
                    app_type=item.app_type,
                    verdict=verdict,  # type: ignore[arg-type]
                    detail=detail,
                    last_backup_at=last,
                    last_backup_job_id=item.last_backup_job_id,
                    last_backup_job_status=job_status.get(item.last_backup_job_id or -1),
                )
            )

        rows.sort(key=lambda r: (r.client or "", r.instance or "", r.kind, r.name))
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.verdict] = counts.get(row.verdict, 0) + 1
        return ValidationReport(
            commcell=commcell_name,
            sources=["commvault"],
            inventory_at=inventory_at,
            max_age_hours=max_age_hours,
            counts=counts,
            problems=sum(n for verdict, n in counts.items() if verdict in PROBLEM_VERDICTS),
            rows=rows,
        )

    app.include_router(router)
    return app
