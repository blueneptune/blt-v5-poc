"""`/v1/assets`: what every backup source says exists, in one shape.

Each product keeps its own inventory table (commvault.source_object,
rubrik.source_object, netbackup.source_object), as each says it: this
reads them side by side and
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
  Rubrik's Do Not Protect, SQL Server's tempdb in NetBackup);
- **unknown**: the source does not say (a Commvault instance, or a row
  inventoried before blt recorded protection).

A NetBackup host's or VM's last backup is the asset service's own where
it gives one, else the newest successful backup job blt holds for it
(blt.api.netbackup.last_backup_from_jobs): NetBackup's asset service
says nothing of a host's backups.
"""

# No `from __future__ import annotations`: see app.py - the routes are
# defined inside a factory, and FastAPI resolves their annotations at
# runtime.

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel
from pydantic import Field as PydanticField
from sqlalchemy import text
from sqlmodel import Session

from .docs import UNAUTHORIZED
from .netbackup import job_backups, last_backup_from_jobs

Kind = Literal["host", "vm", "database", "instance", "file_system", "m365", "other"]
Verdict = Literal["protected", "stale", "unprotected", "excluded", "unknown"]
VERDICTS: tuple[Verdict, ...] = ("protected", "stale", "unprotected", "excluded", "unknown")
SourceType = Literal["commvault", "rubrik", "netbackup"]

# Commvault's kinds, and Rubrik's protection statuses, in blt's words.
_COMMVAULT_KIND = {"client": "host", "instance": "instance", "database": "database", "vm": "vm"}
_RUBRIK_PROTECTION = {"protected": "protected", "nosla": "unprotected", "donotprotect": "excluded"}
# NetBackup's kinds are blt's already (blt.netbackup.assets).
_NETBACKUP_KIND = {"host": "host", "vm": "vm", "instance": "instance", "database": "database"}


def rubrik_kind(object_type: str | None) -> Kind:
    """RSC's object types (VmwareVirtualMachine, Mssql, O365Mailbox,
    LinuxFileset, Ec2Instance, ...) by what they are."""
    t = (object_type or "").lower()
    if t.startswith(("o365", "exchange", "microsoft365")):
        return "m365"
    if "fileset" in t or "volumegroup" in t or t in ("nasshare", "nas"):
        return "file_system"
    if "virtualmachine" in t or t.endswith(("vm", "instance")) or "ec2" in t:
        return "vm"
    if any(
        w in t
        for w in (
            "mssql",
            "oracle",
            "database",
            "db2",
            "saphana",
            "mongo",
            "postgres",
            "mysql",
            "sql",
        )
    ):
        return "database"
    if t in ("physicalhost", "windowshost", "linuxhost", "host"):
        return "host"
    return "other"


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
    source_type: SourceType = PydanticField(
        description="The product: commvault, rubrik or netbackup."
    )
    source_name: str = PydanticField(description="The CommCell, RSC account or primary.")
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
    source_name: str = PydanticField(description="The CommCell, RSC account or primary.")
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


# Every inventory, in one shape. A Commvault row's location is its
# parent's name, and that one's parent's.
_ROWS = text("""
    SELECT o.id::text AS id, 'commvault' AS source_type, c.name AS source_name,
           o.kind AS source_kind, o.name, o.app_type, o.plan, o.protection,
           o.last_backup_at, o.present, o.first_seen_at, o.last_seen_at,
           concat_ws(' / ', NULLIF(gp.name, ''), NULLIF(p.name, '')) AS location
    FROM commvault.source_object o
    JOIN commvault.commcell c ON c.id = o.commcell_id
    LEFT JOIN commvault.source_object p ON p.id = o.parent_id
    LEFT JOIN commvault.source_object gp ON gp.id = p.parent_id
    UNION ALL
    SELECT o.id::text, 'rubrik', a.name, o.kind, o.name, NULL, o.sla_domain_name,
           lower(o.protection_status), o.last_snapshot, o.present, o.first_seen_at,
           o.last_seen_at, o.location
    FROM rubrik.source_object o
    JOIN rubrik.account a ON a.id = o.account_id
    UNION ALL
    SELECT o.id::text, 'netbackup', p.name, o.kind, o.name, o.app_type, o.plan, o.protection,
           o.last_backup_at, o.present, o.first_seen_at, o.last_seen_at, o.location
    FROM netbackup.source_object o
    JOIN netbackup.primary_server p ON p.id = o.primary_id
""")
# What a NetBackup row's last backup is derived from when the asset
# service gives none: its kind, primary, and identifiers.
_NETBACKUP_EXTRA = text("""
    SELECT id::text AS id, primary_id, kind, name, attributes
    FROM netbackup.source_object
    WHERE last_backup_at IS NULL AND kind IN ('host', 'vm')
""")


def _assets(session: Session, now: datetime, stale_days: float) -> list[Asset]:
    out = []
    derived: dict[str, datetime] = {}
    extra = session.execute(_NETBACKUP_EXTRA).mappings().all()
    if extra:
        backups = job_backups(session)
        for row in extra:
            found = last_backup_from_jobs(
                backups, row["primary_id"], row["kind"], row["name"], row["attributes"] or {}
            )
            if found is not None:
                derived[row["id"]] = found[0]
    for row in session.execute(_ROWS).mappings():
        last_backup_at = row["last_backup_at"]
        if row["source_type"] == "commvault":
            kind: Kind = _COMMVAULT_KIND.get(row["source_kind"], "other")  # type: ignore[assignment]
            protection = row["protection"]
        elif row["source_type"] == "netbackup":
            kind = _NETBACKUP_KIND.get(row["source_kind"], "other")  # type: ignore[assignment]
            protection = row["protection"]
            last_backup_at = last_backup_at or derived.get(row["id"])
        else:
            kind = rubrik_kind(row["source_kind"])
            protection = _RUBRIK_PROTECTION.get(row["protection"] or "")
        out.append(
            Asset(
                id=row["id"],
                source_type=row["source_type"],
                source_name=row["source_name"],
                kind=kind,
                source_kind=row["source_kind"],
                name=row["name"],
                location=row["location"] or None,
                app_type=row["app_type"],
                plan=row["plan"],
                protection=protection,
                verdict=verdict(protection, last_backup_at, now, stale_days),
                last_backup_at=last_backup_at,
                present=row["present"],
                first_seen_at=row["first_seen_at"],
                last_seen_at=row["last_seen_at"],
            )
        )
    return out


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
        stale_days: StaleDays = 7,
        source_type: Annotated[SourceType | None, Query(description="One product.")] = None,
        source: Annotated[
            str | None, Query(description="One CommCell, account or primary.")
        ] = None,
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
        """By source, kind, then name. Worst first is the reader's choice:
        filter on `verdict`."""
        rows = _assets(session, datetime.now(UTC), stale_days)
        wanted = name.lower() if name else None
        rows = [
            r
            for r in rows
            if r.present == present
            and (source_type is None or r.source_type == source_type)
            and (source is None or r.source_name == source)
            and (not kind or r.kind in kind)
            and (not verdict_ or r.verdict in verdict_)
            and (wanted is None or wanted in r.name.lower())
        ]
        rows.sort(key=lambda r: (r.source_type, r.source_name, r.kind, r.name.lower()))
        return rows[offset : offset + limit]

    @router.get("/summary", summary="How many assets of each kind, by source and verdict")
    def summary(session: SessionDep, stale_days: StaleDays = 7) -> AssetSummary:
        """Counts of what the latest inventories list (present rows only)."""
        counts: dict[tuple[Any, ...], int] = {}
        last: dict[str, datetime | None] = {}
        for r in _assets(session, datetime.now(UTC), stale_days):
            key = f"{r.source_type}/{r.source_name}"
            last[key] = max(filter(None, (last.get(key), r.last_seen_at)), default=None)
            if not r.present:
                continue
            k = (r.source_type, r.source_name, r.kind, r.verdict)
            counts[k] = counts.get(k, 0) + 1
        return AssetSummary(
            stale_days=stale_days,
            counts=[
                AssetCount(source_type=t, source_name=n, kind=k, verdict=v, count=c)
                for (t, n, k, v), c in sorted(counts.items())
            ],
            last_inventory=last,  # type: ignore[arg-type]
        )

    return router
