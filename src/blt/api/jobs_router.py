"""`/v1/jobs`: every product's jobs in their common shape, from
`common.job` (docs/common-jobs.md, step 3) - what the jobs page reads.
"""

# No `from __future__ import annotations`: see app.py - the routes are
# defined inside a function and name closure-local dependencies.

import uuid
from collections.abc import Callable, Iterator
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import BaseModel
from pydantic import Field as PydanticField
from sqlalchemy import cast, func
from sqlalchemy import select as sa_select
from sqlalchemy.types import Date
from sqlmodel import Session, col, select

from blt.common.mapping import KINDS, STATES

from .docs import UNAUTHORIZED, not_found
from .jobs import CommonJob
from .models import Commcell, Job
from .netbackup import NetBackupJob, NetBackupPrimary
from .rubrik import RubrikAccount, RubrikJob
from .sources import Source

# What /v1/jobs/summary may group by.
GROUPS = ("day", "source", "kind", "protection")
Group = Literal["day", "source", "kind", "protection"]


class CommonJobOut(BaseModel):
    """A job from any product, in blt's common shape. The product's own
    record is at `product_path`."""

    id: uuid.UUID = PydanticField(description="The blt job id.")
    source_id: uuid.UUID = PydanticField(description="The source's id (`GET /v1/sources`).")
    source_type: str = PydanticField(description="The product.", examples=["commvault"])
    source_name: str = PydanticField(description="blt's name for the source.", examples=["prod"])
    product_job_key: str = PydanticField(
        description="The product's own id for the job, as text.", examples=["4711"]
    )
    product_path: str = PydanticField(
        description="Where the product's full record of the job is, in this API.",
        examples=["/v1/sources/commvault/prod/jobs/4711"],
    )
    kind: str = PydanticField(description=f"One of: {', '.join(KINDS)}.", examples=["backup"])
    state: str = PydanticField(description=f"One of: {', '.join(STATES)}.", examples=["failed"])
    product_status: str = PydanticField(
        description="The product's own word for its status, untouched.", examples=["Failed"]
    )
    is_active: bool = PydanticField(description="Will it still change.")
    started_at: datetime | None = PydanticField(default=None, description="When it started.")
    ended_at: datetime | None = PydanticField(
        default=None, description="When it ended; none while it is active."
    )
    updated_at: datetime | None = PydanticField(
        default=None, description="When the product last changed it."
    )
    asset_name: str | None = PydanticField(
        default=None, description="What it ran on, in the product's words.", examples=["web01"]
    )
    asset_type: str | None = PydanticField(
        default=None, description="What sort of thing that is.", examples=["Linux File System"]
    )
    protection: str | None = PydanticField(
        default=None,
        description="What made it run: Commvault plan, Rubrik SLA domain, ...",
        examples=["Gold"],
    )
    bytes_transferred: int | None = PydanticField(
        default=None, description="Written to backup storage."
    )
    bytes_protected: int | None = PydanticField(default=None, description="The data it covered.")
    message: str | None = PydanticField(default=None, description="Why it failed or warned.")
    parent_id: uuid.UUID | None = PydanticField(
        default=None, description="The blt job id of its parent, where the product has one."
    )
    source_object_id: uuid.UUID | None = PydanticField(
        default=None, description="The inventory row it ran on (the product's `objects`)."
    )
    attempt: int | None = PydanticField(default=None, description="Which try this is.")
    first_seen_at: datetime = PydanticField(description="When blt first collected it.")
    last_collected_at: datetime = PydanticField(description="When blt last collected it.")


class CommonJobDetail(CommonJobOut):
    """One job, with the product's full record of it."""

    product: dict[str, Any] = PydanticField(
        description="The product's record, as its own `GET` at `product_path` returns it."
    )


class SummaryRow(BaseModel):
    """How many jobs of one group ended up in each state."""

    day: date | None = PydanticField(
        default=None, description="The day the jobs started (UTC), when grouped by day."
    )
    source_type: str | None = PydanticField(default=None, description="When grouped by source.")
    source_name: str | None = PydanticField(default=None, description="When grouped by source.")
    kind: str | None = PydanticField(default=None, description="When grouped by kind.")
    protection: str | None = PydanticField(default=None, description="When grouped by protection.")
    total: int = PydanticField(description="Jobs in the group.")
    states: dict[str, int] = PydanticField(
        description="Jobs per state; states with none are left out.",
        examples=[{"succeeded": 41, "failed": 2}],
    )


def _product_path(source_type: str, source_name: str, key: str) -> str:
    return f"/v1/sources/{source_type}/{source_name}/jobs/{key}"


def _out(job: CommonJob, source: Source) -> CommonJobOut:
    return CommonJobOut(
        **job.model_dump(),
        source_type=source.type,
        source_name=source.name,
        product_path=_product_path(source.type, source.name, job.product_job_key),
    )


def jobs_router(
    get_session: Callable[[], Iterator[Session]], require_api_key: Callable[..., None]
) -> APIRouter:
    SessionDep = Annotated[Session, Depends(get_session)]
    router = APIRouter(
        prefix="/jobs",
        tags=["jobs"],
        dependencies=[Depends(require_api_key)],
        responses=UNAUTHORIZED,
    )

    # The filters /v1/jobs and /v1/jobs/summary share.
    def filters(
        source_type: Annotated[
            list[str] | None,
            Query(description="Only these products; repeat for several.", examples=["rubrik"]),
        ] = None,
        source: Annotated[
            list[str] | None,
            Query(description="Only these sources, by blt's name; repeat for several."),
        ] = None,
        kind: Annotated[
            list[str] | None, Query(description=f"Only these kinds: {', '.join(KINDS)}.")
        ] = None,
        state: Annotated[
            list[str] | None, Query(description=f"Only these states: {', '.join(STATES)}.")
        ] = None,
        active: Annotated[
            bool | None, Query(description="Only jobs still running (true) or finished (false).")
        ] = None,
        since: Annotated[
            datetime | None, Query(description="Only jobs that started at or after this time.")
        ] = None,
        until: Annotated[
            datetime | None, Query(description="Only jobs that started before this time.")
        ] = None,
        protection: Annotated[
            str | None, Query(description="Only this protection (plan, SLA domain), exactly.")
        ] = None,
        asset: Annotated[
            str | None, Query(description="Part of the asset's name, any case.")
        ] = None,
    ) -> list[Any]:
        conditions: list[Any] = []
        if source_type:
            conditions.append(col(Source.type).in_(source_type))
        if source:
            conditions.append(col(Source.name).in_(source))
        if kind:
            conditions.append(col(CommonJob.kind).in_(kind))
        if state:
            conditions.append(col(CommonJob.state).in_(state))
        if active is not None:
            conditions.append(col(CommonJob.is_active).is_(active))
        if since is not None:
            conditions.append(col(CommonJob.started_at) >= since)
        if until is not None:
            conditions.append(col(CommonJob.started_at) < until)
        if protection is not None:
            conditions.append(col(CommonJob.protection) == protection)
        if asset:
            conditions.append(col(CommonJob.asset_name).ilike(f"%{asset}%"))
        return conditions

    Filters = Annotated[list[Any], Depends(filters)]

    @router.get("", summary="List jobs from every product")
    def list_jobs(
        session: SessionDep,
        conditions: Filters,
        response: Response,
        limit: Annotated[int, Query(ge=1, le=1000, description="Page size.")] = 100,
        offset: Annotated[int, Query(ge=0, description="Rows to skip.")] = 0,
    ) -> list[CommonJobOut]:
        """Jobs from every source, newest start first (jobs not yet started
        first of all). The `X-Total-Count` header holds how many match,
        for paging."""
        joined = select(CommonJob, Source).join(Source, col(Source.id) == CommonJob.source_id)
        total = session.exec(
            select(func.count())
            .select_from(CommonJob)
            .join(Source, col(Source.id) == CommonJob.source_id)
            .where(*conditions)
        ).one()
        response.headers["X-Total-Count"] = str(total)
        rows = session.exec(
            joined.where(*conditions)
            .order_by(col(CommonJob.started_at).desc().nulls_first(), col(CommonJob.id))
            .offset(offset)
            .limit(limit)
        ).all()
        return [_out(job, source) for job, source in rows]

    @router.get("/summary", summary="Count jobs by state, grouped")
    def summary(
        session: SessionDep,
        conditions: Filters,
        group: Annotated[
            list[Group] | None,
            Query(
                description="What to group by; repeat for several. Default: day and source. "
                "Grouping by protection gives the jobs page's rollup - an M365 backup's 1,000 "
                "mailbox jobs as one line."
            ),
        ] = None,
    ) -> list[SummaryRow]:
        """Jobs per state for each group, newest day first. Takes the same
        filters as `GET /v1/jobs`; jobs not yet started have no day."""
        groups = list(dict.fromkeys(group or ["day", "source"]))
        columns: dict[str, Any] = {}
        if "day" in groups:
            columns["day"] = cast(func.timezone("UTC", CommonJob.started_at), Date)
        if "source" in groups:
            columns["source_type"] = Source.type
            columns["source_name"] = Source.name
        if "kind" in groups:
            columns["kind"] = CommonJob.kind
        if "protection" in groups:
            columns["protection"] = CommonJob.protection
        keys = [c.label(name) for name, c in columns.items()]
        query = (
            # SQLAlchemy's own select: sqlmodel's is typed for up to four columns.
            sa_select(*keys, col(CommonJob.state), func.count().label("n"))
            .select_from(CommonJob)
            .join(Source, col(Source.id) == CommonJob.source_id)
            .where(*conditions)
            .group_by(*keys, col(CommonJob.state))
        )
        result: dict[tuple[Any, ...], SummaryRow] = {}
        for row in session.execute(query).all():
            values = {name: getattr(row, name) for name in columns}
            key = tuple(values.values())
            out = result.setdefault(key, SummaryRow(**values, total=0, states={}))
            out.states[row.state] = row.n
            out.total += row.n

        def order(row: SummaryRow) -> tuple[Any, ...]:
            # Newest day first, then by name; a missing day (not yet
            # started) first of all, as in the list.
            day = -row.day.toordinal() if row.day else -(10**9)
            return (day, row.source_type or "", row.source_name or "", row.kind or "",
                    row.protection or "")  # fmt: skip

        return sorted(result.values(), key=order)

    @router.get("/{job_id}", summary="One job", responses=not_found("job"))
    def get_job(
        job_id: Annotated[uuid.UUID, Path(description="The blt job id.")],
        session: SessionDep,
    ) -> CommonJobDetail:
        """One job in the common shape, with the product's full record of
        it in `product`."""
        row = session.exec(
            select(CommonJob, Source)
            .join(Source, col(Source.id) == CommonJob.source_id)
            .where(CommonJob.id == job_id)
        ).first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown job {job_id}")
        job, source = row
        product: Any = None
        if source.type == "commvault":
            product = session.exec(
                select(Job)
                .join(Commcell, col(Commcell.id) == Job.commcell_id)
                .where(Commcell.source_id == source.id, Job.job_id == int(job.product_job_key))
            ).first()
        elif source.type == "rubrik":
            product = session.exec(
                select(RubrikJob)
                .join(RubrikAccount, col(RubrikAccount.id) == RubrikJob.account_id)
                .where(
                    RubrikAccount.source_id == source.id,
                    RubrikJob.series_key == job.product_job_key,
                )
            ).first()
        elif source.type == "netbackup":
            product = session.exec(
                select(NetBackupJob)
                .join(NetBackupPrimary, col(NetBackupPrimary.id) == NetBackupJob.primary_id)
                .where(
                    NetBackupPrimary.source_id == source.id,
                    NetBackupJob.job_id == int(job.product_job_key),
                )
            ).first()
        return CommonJobDetail(
            **_out(job, source).model_dump(),
            product=product.model_dump(mode="json") if product is not None else {},
        )

    return router
