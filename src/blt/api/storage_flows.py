"""Where NetBackup's data goes, for the Storage page's Sankey: what each
primary's backups wrote into each disk pool, and what was copied from
one pool to another - duplicated on the same primary, or replicated to
another primary (AIR) - over the last days. Each pool comes with how
full it is, so a pool filling up shows with what is filling it.

Two columns: where data comes from on the left ("Backups on <primary>",
"Copies from <pool>"), the pools it lands in on the right. A pool is on
the right once, whichever way data reaches it, so two primaries that
replicate to each other draw no loop.

Bytes are what NetBackup's jobs say they wrote (kilobytes_transferred),
before deduplication: the pressure on a pool, not what it took on disk.

How a job finds its pool: a disk pool's storage units are in its
`storage_units` attribute (blt.storage.netbackup); a job names the
storage unit it wrote to. A replication job names the pool it read from
on its own primary; the pool it wrote to is on the other primary, and is
found from that primary's import of the same image (same backup id). A
replication with no import collected yet is counted as unmatched and
drawn to the storage unit it names.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlmodel import Session, col, select

from .sources import Source
from .storage import Storage, StorageOut, _outs

BACKUP_TYPES = ["BACKUP", "DBBACKUP", "ARCHIVE"]
IMPORT_TYPES = ["IMPORT", "REPLICA_IMPORT"]
# Done, and wrote something: 0 succeeded, 1 partly.
GOOD = (0, 1)

FlowKind = Literal["backup", "duplicate", "replicate"]


class FlowNode(BaseModel):
    """One end of a flow: where data comes from (column 0), or the pool or
    storage unit it goes to (column 1)."""

    id: str = Field(description="The node's id within this answer.")
    column: Literal[0, 1] = Field(description="0: where data comes from; 1: where it lands.")
    label: str = Field(description="What to call it.")
    source_name: str = Field(description="The NetBackup primary it is on.")
    storage_id: uuid.UUID | None = Field(
        default=None, description="The storage target (`targets`), when it is a known pool."
    )


class FlowLink(BaseModel):
    """Data that went from one node to another, summed over the window."""

    source: str = Field(description="The node data comes from.")
    target: str = Field(description="The node it lands in.")
    kind: FlowKind = Field(description="backup, duplicate (same primary) or replicate (AIR).")
    bytes: int = Field(description="What the jobs wrote, before deduplication.")
    jobs: int = Field(description="How many jobs.")


class StorageFlows(BaseModel):
    """Where NetBackup's data went in the window, and every pool's use."""

    since: datetime = Field(description="Jobs that ended from here...")
    until: datetime = Field(description="...to here.")
    nodes: list[FlowNode] = Field(description="Where data came from and where it landed.")
    links: list[FlowLink] = Field(description="How much went from each to each.")
    targets: list[StorageOut] = Field(
        description="Every NetBackup pool, with its use and trend - also those nothing "
        "wrote to in the window."
    )
    unmatched_replications: int = Field(
        description="Replication jobs whose import on the other primary is not collected "
        "(yet): drawn to the storage unit the job names."
    )


_BACKUPS = text(
    """
    WITH done AS (
        SELECT j.primary_id, j.job_id, j.parent_job_id, j.storage_unit, j.kilobytes_transferred
        FROM netbackup.job j
        WHERE j.job_type = ANY(:types) AND j.state = 'DONE' AND j.status = ANY(:good)
          AND j.start_time >= :since - interval '7 days'
          AND j.end_time >= :since AND j.end_time < :until
    ),
    parents AS (
        SELECT DISTINCT primary_id, parent_job_id AS job_id FROM done
        WHERE parent_job_id IS NOT NULL AND parent_job_id <> job_id
    )
    SELECT p.name AS primary_name, d.storage_unit,
           sum(d.kilobytes_transferred)::bigint AS kb, count(*) AS jobs
    FROM done d JOIN netbackup.primary_server p ON p.id = d.primary_id
    WHERE d.storage_unit IS NOT NULL AND coalesce(d.kilobytes_transferred, 0) > 0
      -- A parent (multi-stream, VMware) can carry its children's total:
      -- count the children only.
      AND (d.primary_id, d.job_id) NOT IN (SELECT primary_id, job_id FROM parents)
    GROUP BY 1, 2
    """
)

_COPIES = text(
    """
    SELECT p.name AS primary_name, j.job_type, j.source_storage_unit, j.storage_unit,
           imp.primary_name AS to_primary, imp.storage_unit AS to_storage_unit,
           sum(j.kilobytes_transferred)::bigint AS kb, count(*) AS jobs
    FROM netbackup.job j
    JOIN netbackup.primary_server p ON p.id = j.primary_id
    LEFT JOIN LATERAL (
        SELECT ip.name AS primary_name, i.storage_unit
        FROM netbackup.job i JOIN netbackup.primary_server ip ON ip.id = i.primary_id
        WHERE j.job_type = 'REPLICATE' AND i.backup_id = j.backup_id
          AND i.primary_id <> j.primary_id AND i.job_type = ANY(:imports)
          AND i.state = 'DONE' AND i.storage_unit IS NOT NULL
        ORDER BY i.end_time DESC NULLS LAST
        LIMIT 1
    ) imp ON true
    WHERE j.job_type IN ('DUPLICATE', 'REPLICATE') AND j.state = 'DONE'
      AND j.status = ANY(:good)
      AND j.start_time >= :since - interval '7 days'
      AND j.end_time >= :since AND j.end_time < :until
      AND coalesce(j.kilobytes_transferred, 0) > 0
    GROUP BY 1, 2, 3, 4, 5, 6
    """
)


def storage_flows(session: Session, days: float, now: datetime | None = None) -> StorageFlows:
    until = now or datetime.now(UTC)
    since = until - timedelta(days=days)
    rows = list(
        session.exec(
            select(Storage, Source)
            .join(Source, col(Storage.source_id) == col(Source.id))
            .where(Source.type == "netbackup", col(Storage.present).is_(True))
            .order_by(col(Source.name), col(Storage.name))
        )
    )
    targets = _outs(session, rows)
    # (primary, storage unit) -> its pool
    pool_of: dict[tuple[str, str], StorageOut] = {}
    for target in targets:
        for unit in target.attributes.get("storage_units") or []:
            pool_of[(target.source_name, str(unit))] = target

    nodes: dict[str, FlowNode] = {}
    links: dict[tuple[str, str, str], list[int]] = {}

    def landing(primary: str, unit: str | None, note: str = "") -> str:
        pool = pool_of.get((primary, unit or ""))
        if pool is not None:
            node_id = f"pool:{pool.id}"
            nodes.setdefault(
                node_id,
                FlowNode(
                    id=node_id,
                    column=1,
                    label=pool.name,
                    source_name=primary,
                    storage_id=pool.id,
                ),
            )
            return node_id
        node_id = f"unit:{primary}:{unit or '?'}{note}"
        nodes.setdefault(
            node_id,
            FlowNode(id=node_id, column=1, label=f"{unit or 'unknown'}{note}", source_name=primary),
        )
        return node_id

    def origin(node_id: str, label: str, primary: str) -> str:
        nodes.setdefault(node_id, FlowNode(id=node_id, column=0, label=label, source_name=primary))
        return node_id

    def add(source: str, target: str, kind: str, kb: int, jobs: int) -> None:
        total = links.setdefault((source, target, kind), [0, 0])
        total[0] += kb * 1024
        total[1] += jobs

    params = {"since": since, "until": until, "good": list(GOOD)}
    for row in session.execute(_BACKUPS, {**params, "types": BACKUP_TYPES}).mappings():
        primary = row["primary_name"]
        add(
            origin(f"backups:{primary}", f"Backups on {primary}", primary),
            landing(primary, row["storage_unit"]),
            "backup",
            row["kb"],
            row["jobs"],
        )

    unmatched = 0
    for row in session.execute(_COPIES, {**params, "imports": IMPORT_TYPES}).mappings():
        primary = row["primary_name"]
        from_pool = pool_of.get((primary, row["source_storage_unit"] or ""))
        if from_pool is not None:
            source = origin(f"copies:{from_pool.id}", f"Copies from {from_pool.name}", primary)
        else:
            unit = row["source_storage_unit"] or "unknown"
            source = origin(f"copies:{primary}:{unit}", f"Copies from {unit}", primary)
        if row["job_type"] == "DUPLICATE":
            to, kind = landing(primary, row["storage_unit"]), "duplicate"
        elif row["to_primary"] is not None:
            to, kind = landing(row["to_primary"], row["to_storage_unit"]), "replicate"
        else:
            unmatched += row["jobs"]
            to = landing(primary, row["storage_unit"], " (replica, import not seen)")
            kind = "replicate"
        add(source, to, kind, row["kb"], row["jobs"])

    return StorageFlows(
        since=since,
        until=until,
        nodes=sorted(nodes.values(), key=lambda n: (n.column, n.source_name, n.label)),
        links=[
            FlowLink(source=s, target=t, kind=k, bytes=b, jobs=n)  # type: ignore[arg-type]
            for (s, t, k), (b, n) in sorted(links.items())
        ],
        targets=targets,
        unmatched_replications=unmatched,
    )
