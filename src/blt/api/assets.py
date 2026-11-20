"""`/v1/assets`: what every backup source says exists, in one shape.

Each product keeps its own inventory table (commvault.source_object,
rubrik.source_object), as each says it: this reads them side by side and
says, of every row, the one thing the Assets page asks - is it backed
up? Nothing is merged across sources here: a server that is a Commvault
client and a Rubrik VM is two rows (docs/asset-refinery.md is about
linking them).

The verdict:

- **protected**: the source protects it, and its last backup is within
  `stale_days`;
- **stale**: the source protects it, but has no backup that recent;
- **unprotected**: the source knows of it and nothing protects it - the
  "discovered, not backed up" list;
- **excluded**: deliberately left out (Commvault's excluded SLA,
  Rubrik's Do Not Protect);
- **unknown**: the source does not say (a Commvault instance, or a row
  inventoried before blt recorded protection).
"""

# No `from __future__ import annotations`: see app.py - the routes are
# defined inside a factory, and FastAPI resolves their annotations at
# runtime.

import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import AwareDatetime, BaseModel
from pydantic import Field as PydanticField
from sqlalchemy import text
from sqlmodel import Session

from .docs import UNAUTHORIZED

Kind = Literal["host", "vm", "database", "instance", "file_system", "m365", "other"]
Verdict = Literal["protected", "stale", "unprotected", "excluded", "unknown"]
VERDICTS: tuple[Verdict, ...] = ("protected", "stale", "unprotected", "excluded", "unknown")
SourceType = Literal["commvault", "rubrik"]

# Rubrik's object types (VmwareVirtualMachine, Mssql, O365Mailbox,
# LinuxFileset, Ec2Instance, ...) by what they are: the first rule with a
# pattern matching the lower-cased type wins. LIKE patterns, so the same
# rules sort a row in Postgres (_rubrik_kind_sql) and here (rubrik_kind).
_RUBRIK_KINDS: tuple[tuple[Kind, tuple[str, ...]], ...] = (
    ("m365", ("o365%", "exchange%", "microsoft365%")),
    ("file_system", ("%fileset%", "%volumegroup%", "nasshare", "nas")),
    ("vm", ("%virtualmachine%", "%vm", "%instance", "%ec2%")),
    (
        "database",
        (
            "%mssql%",
            "%oracle%",
            "%database%",
            "%db2%",
            "%saphana%",
            "%mongo%",
            "%postgres%",
            "%mysql%",
            "%sql%",
        ),
    ),
    ("host", ("physicalhost", "windowshost", "linuxhost", "host")),
)


def _like(pattern: str) -> re.Pattern[str]:
    return re.compile("^" + ".*".join(re.escape(p) for p in pattern.split("%")) + "$")


def rubrik_kind(object_type: str | None) -> Kind:
    t = (object_type or "").lower()
    for kind, patterns in _RUBRIK_KINDS:
        if any(_like(p).match(t) for p in patterns):
            return kind
    return "other"


def _rubrik_kind_sql(column: str) -> str:
    whens = " ".join(
        f"WHEN {' OR '.join(f'lower({column}) LIKE {chr(39)}{p}{chr(39)}' for p in patterns)} "
        f"THEN '{kind}'"
        for kind, patterns in _RUBRIK_KINDS
    )
    return f"CASE {whens} ELSE 'other' END"


def verdict(
    protection: str | None, last_backup_at: datetime | None, now: datetime, stale_days: float
) -> Verdict:
    if protection == "excluded":
        return "excluded"
    if protection == "unprotected":
        return "unprotected"
    if protection != "protected":
        return "unknown"
    if last_backup_at is None or last_backup_at < now - timedelta(days=stale_days):
        return "stale"
    return "protected"


class Asset(BaseModel):
    """One thing one source says exists."""

    id: str = PydanticField(description="blt's id for the row, in its product's inventory.")
    source_type: SourceType = PydanticField(description="The product: commvault or rubrik.")
    source_name: str = PydanticField(description="The CommCell or RSC account.")
    kind: Kind = PydanticField(
        description="What it is: host, vm, database, instance, file_system, m365 or other."
    )
    source_kind: str | None = PydanticField(
        description="What the product calls it: client, VmwareVirtualMachine, Mssql, ..."
    )
    name: str = PydanticField(description="Its name, as the source shows it.")
    location: str | None = PydanticField(
        description="Where it is: a database's instance and host, a VM's hypervisor, ..."
    )
    app_type: str | None = PydanticField(
        description='Its engine or platform: "SQL Server", "Oracle", "VMW", ...'
    )
    plan: str | None = PydanticField(description="The plan or SLA domain protecting it.")
    protection: str | None = PydanticField(
        description="protected, unprotected or excluded, as the source sees it."
    )
    verdict: Verdict = PydanticField(description="See the tag's description.")
    last_backup_at: AwareDatetime | None = PydanticField(description="Its latest backup.")
    present: bool = PydanticField(description="Listed by the source's latest inventory.")
    first_seen_at: AwareDatetime = PydanticField(description="When an inventory first listed it.")
    last_seen_at: AwareDatetime = PydanticField(description="When an inventory last listed it.")


class AssetCount(BaseModel):
    """How many of one source's assets of one kind have one verdict."""

    source_type: SourceType = PydanticField(description="The product.")
    source_name: str = PydanticField(description="The CommCell or RSC account.")
    kind: Kind = PydanticField(description="What they are.")
    verdict: Verdict = PydanticField(description="Their verdict.")
    count: int = PydanticField(description="How many.")


class AssetSummary(BaseModel):
    """How many assets each source has of each kind, by verdict."""

    stale_days: float = PydanticField(description="The window the verdicts used.")
    counts: list[AssetCount] = PydanticField(description="One row per source, kind and verdict.")
    last_inventory: dict[str, AwareDatetime | None] = PydanticField(
        description="Each source (type/name) and when its inventory last listed anything."
    )


# Both inventories, in one shape, each row with its kind, protection and
# verdict worked out in Postgres. A Commvault row's location is its
# parent's name, and that one's parent's. :since is now - stale_days.
_ASSETS = f"""
WITH rows AS (
    SELECT o.id::text AS id, 'commvault' AS source_type, c.name AS source_name,
           CASE o.kind WHEN 'client' THEN 'host' WHEN 'instance' THEN 'instance'
                WHEN 'database' THEN 'database' WHEN 'vm' THEN 'vm' ELSE 'other' END AS kind,
           o.kind AS source_kind, o.name, o.app_type, o.plan, o.protection,
           o.last_backup_at, o.present, o.first_seen_at, o.last_seen_at,
           NULLIF(concat_ws(' / ', NULLIF(gp.name, ''), NULLIF(p.name, '')), '') AS location
    FROM commvault.source_object o
    JOIN commvault.commcell c ON c.id = o.commcell_id
    LEFT JOIN commvault.source_object p ON p.id = o.parent_id
    LEFT JOIN commvault.source_object gp ON gp.id = p.parent_id
    UNION ALL
    SELECT o.id::text, 'rubrik', a.name, {_rubrik_kind_sql("o.kind")}, o.kind, o.name, NULL,
           o.sla_domain_name,
           CASE lower(o.protection_status) WHEN 'protected' THEN 'protected'
                WHEN 'nosla' THEN 'unprotected' WHEN 'donotprotect' THEN 'excluded' END,
           o.last_snapshot, o.present, o.first_seen_at, o.last_seen_at, o.location
    FROM rubrik.source_object o
    JOIN rubrik.account a ON a.id = o.account_id
),
assets AS (
    SELECT *,
           CASE WHEN protection = 'excluded' THEN 'excluded'
                WHEN protection = 'unprotected' THEN 'unprotected'
                WHEN protection IS DISTINCT FROM 'protected' THEN 'unknown'
                WHEN last_backup_at IS NULL OR last_backup_at < :since THEN 'stale'
                ELSE 'protected' END AS verdict
    FROM rows
)
"""

_WHERE = """
WHERE present = :present
  AND (CAST(:source_type AS text) IS NULL OR source_type = :source_type)
  AND (CAST(:source AS text) IS NULL OR source_name = :source)
  AND (CAST(:kinds AS text[]) IS NULL OR kind = ANY(CAST(:kinds AS text[])))
  AND (CAST(:verdicts AS text[]) IS NULL OR verdict = ANY(CAST(:verdicts AS text[])))
  AND (CAST(:name AS text) IS NULL OR name ILIKE :name ESCAPE '\\')
"""
_LIST = text(
    _ASSETS
    + "SELECT *, count(*) OVER () AS total FROM assets"
    + _WHERE
    + "ORDER BY source_type, source_name, kind, lower(name), id LIMIT :limit OFFSET :offset"
)
_TOTAL = text(_ASSETS + "SELECT count(*) FROM assets" + _WHERE)

_COUNTS = text(
    _ASSETS
    + """
SELECT source_type, source_name, kind, verdict, count(*) FILTER (WHERE present) AS count,
       max(last_seen_at) AS last_seen
FROM assets GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4
"""
)


def _contains(value: str) -> str:
    """A LIKE pattern for "contains value", its own % and _ taken literally."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


StaleDays = Annotated[
    float,
    Query(gt=0, le=365, description="A protected asset with no backup this recent is stale."),
]


def assets_router(
    get_session: Callable[[], Iterator[Session]], require_api_key: Callable[..., None]
) -> APIRouter:
    SessionDep = Annotated[Session, Depends(get_session)]
    router = APIRouter(
        prefix="/assets",
        tags=["assets"],
        dependencies=[Depends(require_api_key)],
        responses=UNAUTHORIZED,
    )

    @router.get("", summary="Every asset every source knows of, with its verdict")
    def list_assets(
        session: SessionDep,
        response: Response,
        stale_days: StaleDays = 7,
        source_type: Annotated[SourceType | None, Query(description="One product.")] = None,
        source: Annotated[str | None, Query(description="One CommCell or account.")] = None,
        kind: Annotated[list[Kind] | None, Query(description="Repeat for several.")] = None,
        verdict_: Annotated[
            list[Verdict] | None,
            Query(alias="verdict", description="Repeat for several: `?verdict=unprotected`."),
        ] = None,
        name: Annotated[str | None, Query(description="Part of its name, any case.")] = None,
        present: Annotated[
            bool, Query(description="false: things the latest inventory no longer lists.")
        ] = True,
        limit: Annotated[int, Query(ge=1, le=5000, description="Page size.")] = 1000,
        offset: Annotated[int, Query(ge=0, description="Rows to skip.")] = 0,
    ) -> list[Asset]:
        """By source, kind, then name. The `X-Total-Count` header holds
        how many match, for paging."""
        params = {
            "since": datetime.now(UTC) - timedelta(days=stale_days),
            "present": present,
            "source_type": source_type,
            "source": source,
            "kinds": list(kind) if kind else None,
            "verdicts": list(verdict_) if verdict_ else None,
            "name": _contains(name) if name else None,
        }
        rows = session.execute(_LIST, {**params, "limit": limit, "offset": offset}).mappings().all()
        # Past the end there is no row to carry the count; a pager still wants it.
        total = rows[0]["total"] if rows else session.execute(_TOTAL, params).scalar_one()
        response.headers["X-Total-Count"] = str(total)
        return [Asset.model_validate({k: v for k, v in r.items() if k != "total"}) for r in rows]

    @router.get("/summary", summary="How many assets of each kind, by source and verdict")
    def summary(session: SessionDep, stale_days: StaleDays = 7) -> AssetSummary:
        """Counts of what the latest inventories list (present rows only)."""
        rows = session.execute(
            _COUNTS, {"since": datetime.now(UTC) - timedelta(days=stale_days)}
        ).mappings()
        counts: list[AssetCount] = []
        last: dict[str, datetime | None] = {}
        for r in rows:
            key = f"{r['source_type']}/{r['source_name']}"
            last[key] = max(filter(None, (last.get(key), r["last_seen"])), default=None)
            if r["count"]:
                counts.append(
                    AssetCount(
                        source_type=r["source_type"],
                        source_name=r["source_name"],
                        kind=r["kind"],
                        verdict=r["verdict"],
                        count=r["count"],
                    )
                )
        return AssetSummary(
            stale_days=stale_days,
            counts=counts,
            last_inventory=last,  # type: ignore[arg-type]
        )

    return router
