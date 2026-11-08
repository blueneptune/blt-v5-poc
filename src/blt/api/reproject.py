"""Projects every product job into common.job again, from the product
tables: once when common.job is first created (blt-migrate does it when
it finds common.job empty), and after a mapping in blt.common.mapping
changes (`blt-migrate reproject`). Safe to repeat - each row is upserted
by its product key."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from sqlalchemy import Engine, exists
from sqlmodel import Session, SQLModel, col, select

from blt.common.mapping import commvault_job, netbackup_job, rubrik_job

from . import jobs as common_jobs
from .models import Commcell, Job
from .netbackup import NetBackupJob, NetBackupPrimary
from .rubrik import RubrikAccount, RubrikJob

_PAGE = 1000


def _pages(
    session: Session, model: type[SQLModel], owner: Any, owner_id: int | None, key: Any
) -> Iterator[list[dict[str, Any]]]:
    """A source's product jobs in key order, a page at a time."""
    last: Any = None
    while True:
        query = select(model).where(owner == owner_id).order_by(key).limit(_PAGE)
        if last is not None:
            query = query.where(key > last)
        page = list(session.exec(query))
        if not page:
            return
        last = getattr(page[-1], key.key)
        rows = [row.model_dump() for row in page]
        session.expunge_all()
        yield rows


def reproject(engine: Engine, log: Callable[[str], None] = print) -> int:
    total = 0
    with Session(engine) as session:
        for commcell in list(session.exec(select(Commcell))):
            count = 0
            for rows in _pages(session, Job, col(Job.commcell_id), commcell.id, col(Job.job_id)):
                count += common_jobs.project(session, commcell.source_id, rows, commvault_job)
                session.commit()
            common_jobs.link(session, "commvault", commcell.source_id)
            session.commit()
            log(f"commvault {commcell.name}: {count} jobs")
            total += count
        for account in list(session.exec(select(RubrikAccount))):
            count = 0
            for rows in _pages(
                session,
                RubrikJob,
                col(RubrikJob.account_id),
                account.id,
                col(RubrikJob.series_key),
            ):
                count += common_jobs.project(session, account.source_id, rows, rubrik_job)
                session.commit()
            common_jobs.link(session, "rubrik", account.source_id)
            session.commit()
            log(f"rubrik {account.name}: {count} jobs")
            total += count
        for primary in list(session.exec(select(NetBackupPrimary))):
            count = 0
            for rows in _pages(
                session,
                NetBackupJob,
                col(NetBackupJob.primary_id),
                primary.id,
                col(NetBackupJob.job_id),
            ):
                count += common_jobs.project(session, primary.source_id, rows, netbackup_job)
                session.commit()
            log(f"netbackup {primary.name}: {count} jobs")
            total += count
    return total


def needs_first_projection(engine: Engine) -> bool:
    """common.job is empty while a product has jobs: it has just been
    created, on a database that already holds collected jobs."""
    with Session(engine) as session:
        if session.exec(select(exists().select_from(common_jobs.CommonJob))).one():
            return False
        return bool(
            session.exec(select(exists().select_from(Job))).one()
            or session.exec(select(exists().select_from(RubrikJob))).one()
            or session.exec(select(exists().select_from(NetBackupJob))).one()
        )
