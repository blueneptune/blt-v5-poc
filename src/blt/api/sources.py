"""Every source blt collects from, of any product, in the `common` schema
(docs/common-jobs.md, step 1): one row per CommCell, RSC account, and
later NetBackup primary or Veeam server, each with a UUID that is its id
everywhere - for links, and for systems outside blt.

The product tables point up to it (`commvault.commcell.source_id`,
`rubrik.account.source_id`); nothing here points down. The API paths
keep using the name (`/v1/sources/rubrik/work`); the id is for the rest.
"""

# No `from __future__ import annotations`: see app.py - the routes are
# defined inside a function and name closure-local dependencies.

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from pydantic import Field as PydanticField
from sqlalchemy import Column, DateTime, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Field, Session, SQLModel, col, select

from .docs import UNAUTHORIZED

SCHEMA = "common"

# The products blt has a collector for. A source's `type` is one of these.
SOURCE_TYPES = ("commvault", "rubrik", "netbackup")


def _ts(*, nullable: bool = True) -> Any:
    return Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=nullable))


class Source(SQLModel, table=True):
    __tablename__ = "source"
    __table_args__ = (
        UniqueConstraint("type", "name", name="uq_common_source_type_name"),
        {"schema": SCHEMA},
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    type: str
    # blt's name for it, as its product table and Prefect block have it.
    name: str
    created_at: datetime = _ts(nullable=False)


def ensure_source(session: Session, type_: str, name: str) -> uuid.UUID:
    """The id of the (type, name) source, made if it is new. ON CONFLICT
    DO NOTHING, so two first-ever runs cannot race into a unique error."""
    session.execute(
        pg_insert(Source)
        .values(id=uuid.uuid4(), type=type_, name=name, created_at=datetime.now(UTC))
        .on_conflict_do_nothing(constraint="uq_common_source_type_name")
    )
    return session.exec(select(Source.id).where(Source.type == type_, Source.name == name)).one()


class SourceOut(BaseModel):
    """A source blt collects from."""

    id: uuid.UUID = PydanticField(description="The source's id, the same in every table.")
    type: str = PydanticField(description="The product.", examples=["commvault"])
    name: str = PydanticField(
        description="blt's name for it - the name in its `/v1/sources/{type}/{name}` paths.",
        examples=["cv-toaster"],
    )
    created_at: datetime = PydanticField(description="When blt first collected from it.")
    last_collected_at: datetime | None = PydanticField(
        default=None,
        description=(
            "When its latest succeeded collection run (of any mode) finished; null if none has."
        ),
    )
    overdue: bool = PydanticField(
        default=False,
        description=(
            "No collection run has succeeded within `overdue_hours` - counting from when the"
            " source was first seen, if none ever has."
        ),
    )
    last_jobs_collected_at: datetime | None = PydanticField(
        default=None,
        description="When its latest succeeded job-collection run (any mode but inventory) ended.",
    )
    jobs_overdue: bool = PydanticField(
        default=False,
        description=(
            "No job-collection run has succeeded within `overdue_hours` (counting from when the"
            " source was first seen, if none ever has). With `overdue` false, its inventory runs"
            " are getting through while its job collection is not."
        ),
    )
    last_jobs_status: str | None = PydanticField(
        default=None,
        description="How its latest job-collection run went: running, succeeded, failed, ...",
    )
    last_inventory_status: str | None = PydanticField(
        default=None, description="How its latest inventory run went; null if it never had one."
    )


# Per source, over every product's runs: the latest succeeded run of any
# mode and of job collection (every mode but inventory), and how the
# latest job-collection and inventory runs went.
_PER_PRODUCT = """
    SELECT {parent}.source_id,
        max(r.finished_at) FILTER (WHERE r.status = 'succeeded') AS at,
        max(r.finished_at) FILTER (WHERE r.status = 'succeeded' AND r.mode <> 'inventory')
            AS jobs_at,
        (array_agg(r.status ORDER BY r.started_at DESC, r.id DESC)
            FILTER (WHERE r.mode <> 'inventory'))[1] AS jobs_status,
        (array_agg(r.status ORDER BY r.started_at DESC, r.id DESC)
            FILTER (WHERE r.mode = 'inventory'))[1] AS inventory_status
    FROM {schema}.collection_run r JOIN {schema}.{table} {parent} ON {parent}.id = r.{fk}
    GROUP BY {parent}.source_id
"""
_LAST_COLLECTED = text(
    "\nUNION ALL\n".join(
        _PER_PRODUCT.format(schema=schema, table=table, parent="o", fk=fk)
        for schema, table, fk in (
            ("commvault", "commcell", "commcell_id"),
            ("rubrik", "account", "account_id"),
            ("netbackup", "primary_server", "primary_id"),
        )
    )
)

OverdueHours = Annotated[
    float,
    Query(
        gt=0,
        le=24 * 90,
        description="A source with no succeeded collection run this recent is overdue.",
    ),
]


def sources_router(
    get_session: Callable[[], Iterator[Session]], require_api_key: Callable[..., None]
) -> APIRouter:
    SessionDep = Annotated[Session, Depends(get_session)]
    router = APIRouter(
        tags=["sources"], dependencies=[Depends(require_api_key)], responses=UNAUTHORIZED
    )

    @router.get("/sources", summary="Every source blt collects from")
    def list_sources(
        session: SessionDep,
        type: Annotated[
            str | None, Query(description="Only this product's sources.", examples=["rubrik"])
        ] = None,
        overdue_hours: OverdueHours = 12,
    ) -> list[SourceOut]:
        """Every CommCell, RSC account and NetBackup primary blt has
        collected from, with the id that stands for it everywhere, when it
        was last collected, and whether that is overdue. A source appears
        the first time a run is started for it."""
        query = select(Source).order_by(col(Source.type), col(Source.name))
        if type is not None:
            query = query.where(Source.type == type)
        runs = {row.source_id: row for row in session.execute(_LAST_COLLECTED)}
        since = datetime.now(UTC) - timedelta(hours=overdue_hours)
        out = []
        for source in session.exec(query):
            row = runs.get(source.id)
            at = row.at if row else None
            jobs_at = row.jobs_at if row else None
            out.append(
                SourceOut(
                    id=source.id,
                    type=source.type,
                    name=source.name,
                    created_at=source.created_at,
                    last_collected_at=at,
                    overdue=(at or source.created_at) < since,
                    last_jobs_collected_at=jobs_at,
                    jobs_overdue=(jobs_at or source.created_at) < since,
                    last_jobs_status=row.jobs_status if row else None,
                    last_inventory_status=row.inventory_status if row else None,
                )
            )
        return out

    return router
