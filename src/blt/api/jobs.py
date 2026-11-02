"""`common.job`: every product's jobs in one shape (docs/common-jobs.md,
step 2). A projection - the product tables stay the record. Each product
upsert projects the rows it wrote, in the same transaction, through that
product's function in blt.common.mapping; `blt-common-jobs rebuild`
projects everything again after a mapping changes.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Field, Session, SQLModel, col

from .sources import SCHEMA

Mapper = Callable[[Mapping[str, Any]], dict[str, Any]]

# Postgres takes at most 65,535 parameters a statement; ~20 a row.
_CHUNK = 1000
# Set on insert only.
_INSERT_ONLY = frozenset({"id", "source_id", "product_job_key", "first_seen_at"})


def _ts(*, nullable: bool = True) -> Any:
    return Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=nullable))


def _big() -> Any:
    return Field(default=None, sa_column=Column(BigInteger, nullable=True))


class CommonJob(SQLModel, table=True):
    __tablename__ = "job"
    __table_args__ = (
        UniqueConstraint("source_id", "product_job_key", name="uq_common_job_product_key"),
        Index("ix_common_job_started", "started_at"),
        Index("ix_common_job_source_started", "source_id", "started_at"),
        Index("ix_common_job_state_started", "state", "started_at"),
        Index("ix_common_job_active", "is_active"),
        {"schema": SCHEMA},
    )

    # The blt job id.
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source_id: uuid.UUID = Field(foreign_key=f"{SCHEMA}.source.id")
    # The product's own id for the job, as text.
    product_job_key: str
    kind: str
    state: str
    product_status: str
    is_active: bool
    started_at: datetime | None = _ts()
    ended_at: datetime | None = _ts()
    updated_at: datetime | None = _ts()
    asset_name: str | None = None
    asset_type: str | None = None
    protection: str | None = None
    bytes_transferred: int | None = _big()
    bytes_protected: int | None = _big()
    message: str | None = None
    parent_id: uuid.UUID | None = Field(default=None, foreign_key=f"{SCHEMA}.job.id")
    # The product's source_object it ran on - a row in a table that
    # depends on the product, so no foreign key.
    source_object_id: uuid.UUID | None = None
    attempt: int | None = None
    first_seen_at: datetime = _ts(nullable=False)
    last_collected_at: datetime = _ts(nullable=False)


def project(
    session: Session, source_id: uuid.UUID, rows: Iterable[Mapping[str, Any]], mapper: Mapper
) -> int:
    """Writes the common.job rows for these product rows. Like the
    product upserts, a row collected earlier than what is stored is left
    alone. Returns how many were written. Does not commit."""
    values = [
        {
            **mapper(row),
            "id": uuid.uuid4(),
            "source_id": source_id,
            "first_seen_at": row["first_seen_at"],
        }
        for row in rows
    ]
    written = 0
    for start in range(0, len(values), _CHUNK):
        chunk = values[start : start + _CHUNK]
        stmt = pg_insert(CommonJob).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_common_job_product_key",
            set_={name: stmt.excluded[name] for name in chunk[0] if name not in _INSERT_ONLY},
            where=col(CommonJob.last_collected_at) <= stmt.excluded.last_collected_at,
        )
        written += len(session.execute(stmt.returning(col(CommonJob.id))).all())
    return written


# Which source_object each job ran on, per product. Commvault: the job's
# client (instances are not yet told apart: jobSummary has no instance
# id). Rubrik: the series' object, by RSC's id or global id.
_LINKS = {
    "commvault": """
        UPDATE common.job c SET source_object_id = so.id
        FROM commvault.job j
        JOIN commvault.commcell cc ON cc.id = j.commcell_id
        JOIN commvault.source_object so
          ON so.commcell_id = j.commcell_id AND so.kind = 'client'
         AND so.source_key = j.client_id::text
        WHERE c.source_id = :source_id AND cc.source_id = :source_id
          AND c.product_job_key = j.job_id::text
          AND c.source_object_id IS DISTINCT FROM so.id
    """,
    "rubrik": """
        UPDATE common.job c SET source_object_id = so.id
        FROM rubrik.job j
        JOIN rubrik.account a ON a.id = j.account_id
        JOIN rubrik.source_object so
          ON so.account_id = j.account_id
         AND (so.rsc_id = j.object_id OR so.source_key = j.object_id)
        WHERE c.source_id = :source_id AND a.source_id = :source_id
          AND c.product_job_key = j.series_key
          AND c.source_object_id IS DISTINCT FROM so.id
    """,
}


def link(
    session: Session, product: str, source_id: uuid.UUID, keys: Iterable[str] | None = None
) -> int:
    """Points jobs at the source_object they ran on, where inventory has
    it: the given jobs, or every job of the source. Does not commit."""
    sql = _LINKS[product]
    params: dict[str, Any] = {"source_id": source_id}
    if keys is not None:
        params["keys"] = list(keys)
        if not params["keys"]:
            return 0
        sql += " AND c.product_job_key = ANY(:keys)"
    return session.execute(text(sql), params).rowcount  # type: ignore[attr-defined, no-any-return]
