"""Commvault's account of what exists, as blt ObjectIn rows.

Three levels, yielded parents first so each batch's parents are already
stored: every client, then every instance on those clients, then - for
SQL Server instances - every database.

A database's existence and its protection come from two different
places in Commvault, and are kept apart on purpose:

- *exists*: the content of the instance's subclients. Commvault adds a
  database there when a backup's discovery sees it, system databases
  included.
- *protected*: GET /sql/databases, which carries a last-backup time and
  job per database, for user databases only.

So a database Commvault has seen but never successfully backed up shows
up here with no backup time, which is the gap the validation looks for.
A database created since the last backup ran shows up nowhere: Commvault
has not seen it yet, and nothing here can know better than its source.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sdk_primer import SDKError

from blt.schemas import ObjectIn

from .client import CommvaultClient

SQL_SERVER = "SQL Server"
_PROGRESS_EVERY = 100


def _epoch(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC) if value else None
    except (TypeError, ValueError):
        return None


def _instance_key(instance: dict[str, Any]) -> str:
    """Commvault's instanceId is not unique on its own: every client's
    file system agent has the same "DefaultInstanceName" instance, id 1.
    Client and agent have to be part of the key or fourteen clients'
    instances collapse into one row."""
    return f"{instance['clientId']}/{instance.get('applicationId', 0)}/{instance['instanceId']}"


def iter_inventory(
    commvault: CommvaultClient, problems: list[str] | None = None
) -> Iterator[list[ObjectIn]]:
    """Clients, then instances, then databases.

    On a CommCell with thousands of clients this is thousands of calls,
    made one after another, and some client will always be odd. A client
    or instance that cannot be read is skipped and named in `problems`
    rather than ending the whole inventory; the caller decides what an
    incomplete inventory is allowed to conclude.
    """
    if problems is None:
        problems = []
    clients = commvault.clients.list()
    total = len(clients)
    logger.info("Inventory: {} clients to read", total)

    agents: dict[int, list[str]] = {}
    instances: list[dict[str, Any]] = []
    for count, entity in enumerate(clients, start=1):
        client_id = entity["clientId"]
        try:
            agents[client_id] = commvault.clients.agents(client_id)
            instances.extend(commvault.instances.list(client_id))
        except SDKError as exc:
            problems.append(f"client {entity.get('clientName')}: {exc}")
        if count % _PROGRESS_EVERY == 0 or count == total:
            logger.info("Inventory: read {} of {} clients", count, total)

    yield [
        ObjectIn(
            kind="client",
            source_key=str(entity["clientId"]),
            name=entity.get("clientName", ""),
            attributes={
                "hostName": entity.get("hostName"),
                "displayName": entity.get("displayName"),
                "clientGUID": entity.get("clientGUID"),
                "agents": agents.get(entity["clientId"]),
            },
        )
        for entity in clients
    ]

    # Databases are worked out before instances are sent, because an
    # instance's row carries a fact that comes from them: which job was
    # its latest full, and what the CommServe says that job took.
    databases: list[ObjectIn] = []
    last_full: dict[str, dict[str, Any]] = {}
    sql_instances = [i for i in instances if i.get("appName") == SQL_SERVER]
    logger.info("Inventory: {} SQL Server instances to read", len(sql_instances))
    for count, instance in enumerate(sql_instances, start=1):
        try:
            found = _sql_databases(commvault, instance)
            latest = max((d.last_full_job_id or 0 for d in found), default=0)
            if latest:
                last_full[_instance_key(instance)] = commvault.jobs.counts(latest)
            databases.extend(found)
        except SDKError as exc:
            problems.append(f"SQL instance {instance.get('instanceName')}: {exc}")
        if count % _PROGRESS_EVERY == 0 or count == len(sql_instances):
            logger.info("Inventory: read {} of {} SQL Server instances", count, len(sql_instances))

    yield [
        ObjectIn(
            kind="instance",
            source_key=_instance_key(instance),
            name=instance.get("instanceName", ""),
            parent_kind="client",
            parent_key=str(instance["clientId"]),
            app_type=instance.get("appName"),
            attributes={
                "instanceId": instance["instanceId"],
                "instanceGUID": instance.get("instanceGUID"),
                "last_full_job": last_full.get(_instance_key(instance)),
            },
        )
        for instance in instances
    ]
    yield databases


def _sql_databases(commvault: CommvaultClient, instance: dict[str, Any]) -> list[ObjectIn]:
    instance_id = instance["instanceId"]
    # Exists: named in the content of one of the instance's subclients.
    discovered: dict[str, dict[str, Any]] = {}
    for subclient in commvault.subclients.list(instance["clientId"]):
        if subclient.get("instanceId") != instance_id:
            continue
        properties = commvault.subclients.get(subclient["subclientId"])
        for item in properties.get("content") or []:
            content = item.get("mssqlDbContent") or {}
            if content.get("databaseName"):
                discovered[content["databaseName"]] = {
                    "subclient": subclient.get("subclientName"),
                    "plan": (properties.get("planEntity") or {}).get("planName"),
                    "discoverType": content.get("discoverType"),
                }
    # Protected: listed with a backup time. Anything listed here exists
    # too, even if no subclient content names it.
    backed_up = {d["dbName"]: d for d in commvault.sql.databases(instance_id) if d.get("dbName")}

    result = []
    for name in sorted(set(discovered) | set(backed_up)):
        backup = backed_up.get(name, {})
        result.append(
            ObjectIn(
                kind="database",
                source_key=f"{_instance_key(instance)}/{name}",
                name=name,
                parent_kind="instance",
                parent_key=_instance_key(instance),
                app_type=SQL_SERVER,
                last_backup_at=_epoch(backup.get("bkpTime")),
                last_backup_job_id=backup.get("jobId") or None,
                last_full_job_id=backup.get("fullJobId") or None,
                attributes={**discovered.get(name, {}), "backup": backup},
            )
        )
    return result
