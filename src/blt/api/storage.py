"""Storage monitoring (docs/storage.md): what each source writes backups
to, and how full it is, every hour.

Two tables in the `common` schema, product-neutral like `common.job`:

- `common.storage` - one row per storage target of a source: a Commvault
  storage pool, a NetBackup disk pool, later a Rubrik cluster or a Veeam
  repository. Updated in place by each collection; `present` turns false
  when a complete collection no longer lists it.
- `common.storage_sample` - capacity, used and free bytes of a target at
  one moment. One row per target per collection; never updated.

Collectors post to `/v1/sources/{type}/{name}/storage`; the Storage page
reads `/v1/storage` (each target with its latest sample and trend) and
`/v1/storage/{id}/samples` (its history).
"""

# No `from __future__ import annotations`: see app.py - the routes are
# defined inside a function and name closure-local dependencies.

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel
from pydantic import Field as PydanticField
from sqlalchemy import BigInteger, Column, DateTime, Float, String, UniqueConstraint, func, text
from sqlalchemy import select as sa_select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Field, Session, SQLModel, col, select

from blt.schemas import StorageBatch, StorageIn

from .docs import UNAUTHORIZED, not_found
from .sources import SCHEMA, Source, ensure_source

# How far back the trend looks, and the fewest hours it needs to say
# anything: two samples a minute apart give a slope that means nothing.
TREND_DAYS = 14
TREND_MIN_HOURS = 6


def _ts(*, nullable: bool = True) -> Any:
    return Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=nullable))


def _big() -> Any:
    return Field(default=None, sa_column=Column(BigInteger, nullable=True))


class Storage(SQLModel, table=True):
    __tablename__ = "storage"
    __table_args__ = (
        UniqueConstraint("source_id", "product_key", name="uq_common_storage_product_key"),
        {"schema": SCHEMA},
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source_id: uuid.UUID = Field(foreign_key=f"{SCHEMA}.source.id")
    # The product's own id for it, as text; unique per source.
    product_key: str
    name: str
    kind: str
    product_type: str | None = None
    # MediaAgents / media servers that write to it.
    hosts: list[str] = Field(default_factory=list, sa_column=Column(ARRAY(String), nullable=False))
    # The storage server (NetBackup) or library (Commvault) it is on.
    server: str | None = None
    status: str | None = None
    attributes: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    present: bool = True
    first_seen_at: datetime = _ts(nullable=False)
    last_seen_at: datetime = _ts(nullable=False)


class StorageSample(SQLModel, table=True):
    __tablename__ = "storage_sample"
    __table_args__ = {"schema": SCHEMA}

    storage_id: uuid.UUID = Field(foreign_key=f"{SCHEMA}.storage.id", primary_key=True)
    collected_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), primary_key=True, nullable=False)
    )
    capacity_bytes: int | None = _big()
    used_bytes: int | None = _big()
    free_bytes: int | None = _big()
    logical_bytes: int | None = _big()
    physical_bytes: int | None = _big()
    dedupe_ratio: float | None = Field(default=None, sa_column=Column(Float, nullable=True))


class StorageWritten(BaseModel):
    """What a storage batch did."""

    received: int = PydanticField(description="Targets in the batch.")
    new: int = PydanticField(description="Of those, seen for the first time.")
    samples: int = PydanticField(
        description="Samples stored (a target already sampled at this collected_at is skipped)."
    )
    gone: int = PydanticField(
        description="Targets of the source this complete batch no longer lists, now not present."
    )


class Trend(BaseModel):
    """How fast use is growing, from the samples of the last days."""

    days: float = PydanticField(description="How many days of samples the trend is from.")
    used_bytes_per_day: float = PydanticField(
        description="Growth of used bytes per day (negative when shrinking)."
    )
    days_to_full: float | None = PydanticField(
        default=None,
        description="At that rate, days until free space runs out. None when not growing.",
    )
    full_at: datetime | None = PydanticField(default=None, description="When that would be.")


class SampleOut(BaseModel):
    """A target's capacity and use at one moment."""

    collected_at: datetime = PydanticField(description="When the source was read.")
    capacity_bytes: int | None = PydanticField(default=None, description="Total size.")
    used_bytes: int | None = PydanticField(default=None, description="In use.")
    free_bytes: int | None = PydanticField(default=None, description="Free to write.")
    logical_bytes: int | None = PydanticField(
        default=None, description="Data held, before deduplication and compression."
    )
    physical_bytes: int | None = PydanticField(
        default=None, description="What that data takes on disk."
    )
    dedupe_ratio: float | None = PydanticField(
        default=None, description="logical / physical, where the product says."
    )


class StorageOut(BaseModel):
    """A storage target, with its latest sample and trend."""

    id: uuid.UUID = PydanticField(description="blt's id for the target.")
    source_id: uuid.UUID = PydanticField(description="The source's id (`GET /v1/sources`).")
    source_type: str = PydanticField(description="The product.", examples=["commvault"])
    source_name: str = PydanticField(description="blt's name for the source.")
    product_key: str = PydanticField(description="The product's own id for it, as text.")
    name: str = PydanticField(description="Its name, as the product shows it.")
    kind: str = PydanticField(description="disk, dedupe, cloud, tape, cluster or other.")
    product_type: str | None = PydanticField(
        default=None,
        description="The product's own word for its type.",
        examples=["DEDUPLICATION", "MSDP", "AdvancedDisk"],
    )
    hosts: list[str] = PydanticField(description="MediaAgents / media servers that write to it.")
    server: str | None = PydanticField(
        default=None, description="Storage server (NetBackup) or library (Commvault)."
    )
    status: str | None = PydanticField(default=None, description="As the product words it.")
    present: bool = PydanticField(description="False once a collection no longer lists it.")
    first_seen_at: datetime = PydanticField(description="When blt first saw it.")
    last_seen_at: datetime = PydanticField(description="When a collection last listed it.")
    attributes: dict[str, Any] = PydanticField(description="Whatever else the product says.")
    latest: SampleOut | None = PydanticField(default=None, description="The newest sample.")
    trend: Trend | None = PydanticField(
        default=None,
        description=f"From the last {TREND_DAYS} days; none with under {TREND_MIN_HOURS} hours.",
    )


def _used(sample: StorageIn) -> int | None:
    if sample.used_bytes is not None:
        return sample.used_bytes
    if sample.capacity_bytes is not None and sample.free_bytes is not None:
        return sample.capacity_bytes - sample.free_bytes
    return None


def store_batch(
    session: Session, source_type: str, source_name: str, batch: StorageBatch
) -> StorageWritten:
    """Upsert the targets, add a sample each, and - when the batch says
    it is the whole of the source's storage - mark the rest not present.
    Does not commit."""
    source_id = ensure_source(session, source_type, source_name)
    at = batch.collected_at
    known = {
        row.product_key: row
        for row in session.exec(select(Storage).where(Storage.source_id == source_id))
    }
    new = samples = 0
    for item in batch.targets:
        fields = item.model_dump(
            include={"name", "kind", "product_type", "hosts", "server", "status", "attributes"}
        )
        row = known.get(item.product_key)
        if row is None:
            row = Storage(
                source_id=source_id,
                product_key=item.product_key,
                first_seen_at=at,
                last_seen_at=at,
                **fields,
            )
            session.add(row)
            session.flush()
            new += 1
        elif at >= row.last_seen_at:
            for key, value in fields.items():
                setattr(row, key, value)
            row.present = True
            row.last_seen_at = at
        written = session.execute(
            pg_insert(StorageSample)
            .values(
                storage_id=row.id,
                collected_at=at,
                capacity_bytes=item.capacity_bytes,
                used_bytes=_used(item),
                free_bytes=item.free_bytes,
                logical_bytes=item.logical_bytes,
                physical_bytes=item.physical_bytes,
                dedupe_ratio=item.dedupe_ratio,
            )
            .on_conflict_do_nothing()
            .returning(col(StorageSample.storage_id))
        )
        samples += len(written.all())
    gone = 0
    if batch.complete:
        listed = {item.product_key for item in batch.targets}
        for key, row in known.items():
            if key not in listed and row.present and row.last_seen_at < at:
                row.present = False
                gone += 1
    return StorageWritten(received=len(batch.targets), new=new, samples=samples, gone=gone)


def _trends(
    session: Session, ids: list[uuid.UUID], now: datetime
) -> dict[uuid.UUID, tuple[float, float]]:
    """(days of samples, used bytes per day) per target, by least squares
    over the last TREND_DAYS days."""
    if not ids:
        return {}
    epoch = func.extract("epoch", col(StorageSample.collected_at))
    rows = session.execute(
        sa_select(
            col(StorageSample.storage_id),
            func.regr_slope(col(StorageSample.used_bytes), epoch),
            (func.max(epoch) - func.min(epoch)) / 86400.0,
        )
        .where(
            col(StorageSample.storage_id).in_(ids),
            col(StorageSample.collected_at) >= now - timedelta(days=TREND_DAYS),
            col(StorageSample.used_bytes).is_not(None),
        )
        .group_by(col(StorageSample.storage_id))
    ).all()
    return {
        storage_id: (float(days), float(slope) * 86400.0)
        for storage_id, slope, days in rows
        if slope is not None and days is not None and days * 24 >= TREND_MIN_HOURS
    }


def _trend(days: float, per_day: float, latest: SampleOut | None, now: datetime) -> Trend:
    to_full = None
    if per_day > 0 and latest is not None and latest.free_bytes is not None:
        to_full = latest.free_bytes / per_day
    return Trend(
        days=round(days, 2),
        used_bytes_per_day=per_day,
        days_to_full=None if to_full is None else round(to_full, 1),
        full_at=None if to_full is None or to_full > 36500 else now + timedelta(days=to_full),
    )


def _sample_out(sample: StorageSample) -> SampleOut:
    return SampleOut.model_validate(sample, from_attributes=True)


def _outs(session: Session, targets: list[tuple[Storage, Source]]) -> list[StorageOut]:
    """Each target with its newest sample and its trend."""
    ids = [storage.id for storage, _ in targets]
    latest: dict[uuid.UUID, StorageSample] = {}
    if ids:
        for sample in session.exec(
            select(StorageSample)
            .where(col(StorageSample.storage_id).in_(ids))
            .distinct(col(StorageSample.storage_id))
            .order_by(col(StorageSample.storage_id), col(StorageSample.collected_at).desc())
        ):
            latest[sample.storage_id] = sample
    now = datetime.now(UTC)
    trends = _trends(session, ids, now)
    out = []
    for storage, source in targets:
        newest = latest.get(storage.id)
        sample_out = _sample_out(newest) if newest else None
        trend = trends.get(storage.id)
        out.append(
            StorageOut(
                **storage.model_dump(exclude={"source_id"}),
                source_id=source.id,
                source_type=source.type,
                source_name=source.name,
                latest=sample_out,
                trend=_trend(*trend, sample_out, now) if trend else None,
            )
        )
    return out


StorageId = Annotated[uuid.UUID, Path(description="blt's id for the target (`GET /v1/storage`).")]


def storage_ingest_router(
    source_type: str,
    get_session: Callable[[], Iterator[Session]],
    require_api_key: Callable[..., None],
) -> APIRouter:
    """POST .../storage for one product's sources: the collectors' half."""
    SessionDep = Annotated[Session, Depends(get_session)]
    router = APIRouter(dependencies=[Depends(require_api_key)], responses=UNAUTHORIZED)

    @router.post("/storage", tags=["storage"], summary="Store one collection of storage")
    def post_storage(
        session: SessionDep,
        source_name: Annotated[
            str,
            Path(
                description="blt's name for the source - its Prefect block's name. Made the "
                "first time storage is posted for it.",
                examples=["cv-toaster"],
            ),
        ],
        batch: StorageBatch,
    ) -> StorageWritten:
        """Every storage target the collector read from the source, each
        with its capacity and use at `collected_at`. A target is matched
        by `product_key`; one not seen before is added. With `complete`
        (the default), targets the source no longer lists are marked not
        present - their samples stay."""
        result = store_batch(session, source_type, source_name, batch)
        session.commit()
        return result

    return router


def storage_router(
    get_session: Callable[[], Iterator[Session]], require_api_key: Callable[..., None]
) -> APIRouter:
    """`/v1/storage`: every source's storage, for the Storage page."""
    SessionDep = Annotated[Session, Depends(get_session)]
    router = APIRouter(
        prefix="/storage",
        tags=["storage"],
        dependencies=[Depends(require_api_key)],
        responses=UNAUTHORIZED,
    )

    @router.get("", summary="Every storage target, with its latest use and trend")
    def list_storage(
        session: SessionDep,
        source_type: Annotated[str | None, Query(description="Only this product's.")] = None,
        source_name: Annotated[str | None, Query(description="Only this source's.")] = None,
        kind: Annotated[str | None, Query(description="Only this kind.")] = None,
        present: Annotated[
            bool | None, Query(description="True (default): only targets still listed.")
        ] = True,
    ) -> list[StorageOut]:
        """Each target with its newest sample and, once there are enough
        hours of samples, how fast it is filling and when it would be
        full at that rate."""
        query = select(Storage, Source).join(Source, col(Storage.source_id) == col(Source.id))
        if source_type is not None:
            query = query.where(Source.type == source_type)
        if source_name is not None:
            query = query.where(Source.name == source_name)
        if kind is not None:
            query = query.where(Storage.kind == kind)
        if present is not None:
            query = query.where(Storage.present == present)
        rows = session.exec(query.order_by(col(Source.type), col(Source.name), col(Storage.name)))
        return _outs(session, list(rows))

    from .storage_flows import StorageFlows, storage_flows

    # Before /{storage_id}, which would take "flows" for an id.
    @router.get("/flows", summary="Where NetBackup's data goes: backups and copies per pool")
    def get_flows(
        session: SessionDep,
        days: Annotated[
            float, Query(gt=0, le=90, description="Jobs that ended in the last so many days.")
        ] = 7,
    ) -> StorageFlows:
        """For the Storage page's Sankey: what each NetBackup primary's
        backups wrote into each disk pool, and what was copied from one
        pool to another - duplicated on one primary, or replicated to
        another (matched to that primary's import of the same image) -
        with every pool's use and trend. Bytes as the jobs report them,
        before deduplication."""
        return storage_flows(session, days)

    @router.get(
        "/{storage_id}", summary="One storage target", responses=not_found("storage target")
    )
    def get_storage(session: SessionDep, storage_id: StorageId) -> StorageOut:
        """The target, present or not, with its latest sample and trend."""
        row = session.exec(
            select(Storage, Source)
            .join(Source, col(Storage.source_id) == col(Source.id))
            .where(Storage.id == storage_id)
        ).first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "storage target not found")
        return _outs(session, [row])[0]

    @router.get(
        "/{storage_id}/samples",
        summary="One target's use over time",
        responses=not_found("storage target"),
    )
    def list_samples(
        session: SessionDep,
        storage_id: StorageId,
        days: Annotated[float, Query(gt=0, le=3660, description="How far back.")] = 30,
        every: Annotated[
            Literal["sample", "hour", "day"],
            Query(description="Each sample as stored, or the last of each hour or day."),
        ] = "sample",
    ) -> list[SampleOut]:
        """Oldest first. `every=day` gives one sample a day (the last of
        it), for long ranges."""
        if session.get(Storage, storage_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "storage target not found")
        since = datetime.now(UTC) - timedelta(days=days)
        query = select(StorageSample).where(
            StorageSample.storage_id == storage_id, col(StorageSample.collected_at) >= since
        )
        if every == "sample":
            samples = session.exec(query.order_by(col(StorageSample.collected_at)))
            return [_sample_out(s) for s in samples]
        bucket = func.date_trunc(every, StorageSample.collected_at, text("'UTC'"))
        newest = (
            select(StorageSample)
            .where(StorageSample.storage_id == storage_id, col(StorageSample.collected_at) >= since)
            .distinct(bucket)
            .order_by(bucket, col(StorageSample.collected_at).desc())
        )
        return [_sample_out(s) for s in session.exec(newest)]

    return router
