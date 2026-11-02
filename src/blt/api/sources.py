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
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from pydantic import Field as PydanticField
from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Field, Session, SQLModel, col, select

from .docs import UNAUTHORIZED

SCHEMA = "common"

# The products blt has a collector for. A source's `type` is one of these.
SOURCE_TYPES = ("commvault", "rubrik")


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
    ) -> list[SourceOut]:
        """Every CommCell and RSC account blt has collected from, of any
        product, with the id that stands for it everywhere. A source
        appears the first time a run is started for it."""
        query = select(Source).order_by(col(Source.type), col(Source.name))
        if type is not None:
            query = query.where(Source.type == type)
        return [SourceOut.model_validate(s, from_attributes=True) for s in session.exec(query)]

    return router
