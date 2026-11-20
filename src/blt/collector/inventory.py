"""Commvault's account of what exists, as blt ObjectIn rows.

Yielded parents first so each batch's parents are already stored: every
client (with what /V4/Servers and /V4/FileServers say of it), then every
instance on those clients, then every database - SQL Server's in depth,
every other engine's from /V4/Databases - then every virtual machine
Commvault knows of, protected or only discovered (/V4/VirtualMachines).

Each row carries `protection` - protected, unprotected or excluded, as
Commvault sees it - and `plan`, so "discovered but not backed up" can be
asked of every kind the same way. Commvault knows of a VM only through a
hypervisor it has been given; a VM on a vCenter it was never pointed at
is in nobody's list here.

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

from commvault_sdk import CommvaultClient
from loguru import logger
from sdk_primer import SDKError

from blt.schemas import ObjectIn, Protection

SQL_SERVER = "SQL Server"
_PROGRESS_EVERY = 100
_VM_PAGE = 500

# /V4/Databases' databaseEngine, as the rest of Commvault names it.
_ENGINES = {
    "SQL_SERVER": SQL_SERVER,
    "ORACLE": "Oracle",
    "ORACLE_RAC": "Oracle RAC",
    "POSTGRESQL": "PostgreSQL",
    "MYSQL": "MySQL",
    "MARIA_DB": "MariaDB",
    "AURORA_MYSQL": "Aurora MySQL",
    "SAP_HANA": "SAP HANA",
    "SAP_FOR_ORACLE": "SAP for Oracle",
    "DB2": "DB2",
    "DB2_MULTINODE": "DB2 multinode",
    "SYBASE": "Sybase",
    "INFORMIX": "Informix",
    "DOCUMENT_DB": "DocumentDB",
    "DYNAMO_DB": "DynamoDB",
    "RDS": "RDS",
    "REDSHIFT": "Redshift",
}
# /V4/VirtualMachines' status.
_VM_UNPROTECTED = {"NOT_PROTECTED", "DISCOVERED"}


def _epoch(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC) if value else None
    except (TypeError, ValueError):
        return None


def _name(entity: Any) -> str | None:
    """A {id, name} reference's name; Commvault sends id 0 and "" for none."""
    if isinstance(entity, dict) and entity.get("id") and entity.get("name"):
        return str(entity["name"])
    return None


def protection(sla: str | None, plan: str | None, backed_up: bool) -> Protection:
    """Commvault's view: excluded if its SLA says so, unprotected if it
    says it is yet to be protected or nothing covers it, else protected.
    Whether the backups are recent enough is the reader's question."""
    if sla in ("EXCLUDED_SLA", "EXCLUDED"):
        return "excluded"
    if sla == "YET_TO_BE_PROTECTED":
        return "unprotected"
    return "protected" if plan or backed_up else "unprotected"


def _listed(problems: list[str], what: str, call: Any, key: str) -> list[dict[str, Any]]:
    """One V4 listing; a CommServe that refuses it is a problem, not a stop."""
    try:
        return list((call() or {}).get(key) or [])
    except SDKError as exc:
        problems.append(f"{what}: {exc}")
        return []


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

    raw = commvault.raw
    # Every server, not only infrastructure (the API's default).
    servers = {
        s.get("id"): s
        for s in _listed(
            problems,
            "servers",
            lambda: raw.servers.get_servers(show_only_infrastructure_machines=0),
            "servers",
        )
    }
    file_servers = {
        f.get("id"): f
        for f in _listed(problems, "file servers", raw.file_servers.get_file_servers, "fileServers")
    }
    yield [_client(entity, agents, servers, file_servers) for entity in clients]

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

    instance_keys = {_instance_key(i) for i in instances}
    databases = _merge_databases(
        databases,
        _listed(problems, "databases", raw.databases.get_databases, "databases"),
        instance_keys,
    )

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
    client_ids = {str(entity["clientId"]) for entity in clients}
    yield [_vm(vm, client_ids) for vm in _vms(commvault, problems)]


def _client(
    entity: dict[str, Any],
    agents: dict[int, list[str]],
    servers: dict[Any, dict[str, Any]],
    file_servers: dict[Any, dict[str, Any]],
) -> ObjectIn:
    client_id = entity["clientId"]
    server = servers.get(client_id) or {}
    files = file_servers.get(client_id)
    # The newest successful backup any agent on it has had.
    last = max((a.get("lastSuccessfulBackup") or 0 for a in server.get("agents") or []), default=0)
    plan = sla = None
    if files:
        plan = _name(files.get("plan"))
        sla = (files.get("SLA") or {}).get("status")
        last = max(last, (files.get("lastBackup") or {}).get("lastSuccessfulBackup") or 0)
    return ObjectIn(
        kind="client",
        source_key=str(client_id),
        name=entity.get("clientName", ""),
        last_backup_at=_epoch(last),
        plan=plan,
        protection=protection(sla, plan, bool(last)),
        attributes={
            "hostName": entity.get("hostName"),
            "displayName": entity.get("displayName"),
            "clientGUID": entity.get("clientGUID"),
            "agents": agents.get(client_id),
            "os": server.get("OS"),
            "configured": server.get("configured"),
            "is_infrastructure": server.get("isInfrastructure"),
            "agent_backups": [
                {"name": a.get("name"), "last_successful": a.get("lastSuccessfulBackup")}
                for a in server.get("agents") or []
            ],
            "file_system": (
                {
                    "plan": plan,
                    "sla": files.get("SLA"),
                    "last_backup": files.get("lastBackup"),
                    "configured": files.get("configured"),
                }
                if files
                else None
            ),
        },
    )


def _merge_databases(
    sql: list[ObjectIn], listed: list[dict[str, Any]], instance_keys: set[str]
) -> list[ObjectIn]:
    """SQL Server's databases as read in depth, with /V4/Databases'
    plan and SLA added; every other engine's databases from it alone."""
    by_key = {d.source_key: d for d in sql}
    extra: list[ObjectIn] = []
    for item in listed:
        name = item.get("name")
        server = (item.get("server") or {}).get("id")
        instance = (item.get("instance") or {}).get("id")
        if not name or server is None or instance is None:
            continue
        parent = f"{server}/{item.get('applicationId', 0)}/{instance}"
        key = f"{parent}/{name}"
        plan = _name(item.get("plan"))
        sla = item.get("SLAStatus")
        found = by_key.get(key)
        if found is not None:
            found.plan = plan or found.attributes.get("plan")
            found.protection = protection(sla, found.plan, found.last_backup_at is not None)
            found.attributes["sla"] = sla
            found.attributes["recovery_model"] = item.get("recoveryModel")
            continue
        backed_up = _epoch(item.get("lastBackupTime"))
        extra.append(
            ObjectIn(
                kind="database",
                source_key=key,
                name=name,
                parent_kind="instance" if parent in instance_keys else None,
                parent_key=parent if parent in instance_keys else None,
                app_type=_ENGINES.get(item.get("databaseEngine") or "", item.get("databaseEngine")),
                last_backup_at=backed_up,
                plan=plan,
                protection=protection(sla, plan, backed_up is not None),
                attributes={
                    "server": _name(item.get("server")),
                    "instance": _name(item.get("instance")),
                    "sla": sla,
                    "recovery_model": item.get("recoveryModel"),
                    "last_backup_size": item.get("lastBackupSize"),
                    "vendor": item.get("vendor"),
                },
            )
        )
    for d in sql:
        if d.protection is None:
            d.plan = d.plan or d.attributes.get("plan")
            d.protection = protection(None, d.plan, d.last_backup_at is not None)
    return sql + extra


def _vms(commvault: CommvaultClient, problems: list[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    while True:
        page = _listed(
            problems,
            "virtual machines",
            lambda: commvault.raw.virtual_machines.list_virtual_machines(
                start=len(found), limit=_VM_PAGE
            ),
            "virtualMachines",
        )
        found.extend(page)
        if len(page) < _VM_PAGE:
            break
    if found:
        logger.info("Inventory: {} virtual machines", len(found))
    return found


def _vm(vm: dict[str, Any], client_ids: set[str]) -> ObjectIn:
    hypervisor = str((vm.get("hypervisor") or {}).get("id") or "")
    last = vm.get("lastBackup") or {}
    plan = _name(vm.get("plan"))
    sla = (vm.get("SLA") or {}).get("status")
    backed_up = _epoch(last.get("lastSuccessfulBackupTime"))
    state = (
        "unprotected"
        if vm.get("status") in _VM_UNPROTECTED and sla not in ("EXCLUDED_SLA",)
        else protection(sla, plan, backed_up is not None)
    )
    return ObjectIn(
        kind="vm",
        source_key=str(vm.get("UUID") or vm.get("name")),
        name=vm.get("name", ""),
        parent_kind="client" if hypervisor in client_ids else None,
        parent_key=hypervisor if hypervisor in client_ids else None,
        app_type=vm.get("vendor"),
        last_backup_at=backed_up,
        last_backup_job_id=last.get("jobId") or None,
        plan=plan,
        protection=state,
        attributes={
            "uuid": vm.get("UUID"),
            "host": vm.get("host"),
            "os": vm.get("os"),
            "hypervisor": _name(vm.get("hypervisor")),
            "vm_group": _name(vm.get("vmGroup")),
            "status": vm.get("status"),
            "sla": vm.get("SLA"),
            "last_backup": last,
            "size": vm.get("vmSize"),
            "tags": vm.get("tags"),
        },
    )


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
