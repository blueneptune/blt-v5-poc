"""A NetBackup primary's account of what exists, protected or only
discovered, as blt NetBackupObjectIn rows (docs/assets.md).

Read from NetBackup 10.2's API (yoki-py-netbackup-sdk, spec/10.2):

- VMs (vmware, rhv): GET /asset-service/workloads/{workload}/assets,
  offset-paged;
- SQL Server instances and databases: GET /asset-service/workloads/
  mssql/assets;
- hosts: GET /config/hosts (every host the primary knows), and the
  clients of every host and SQL Server policy;
- policies, for what each protects: GET /config/policies, then each
  host, VM and SQL Server policy in full (GET /config/policies/{name}).

GET /assets (assetdb.yaml), the older asset API, is gone in 10.2: the
spec marks it removed, answering 410, and points at the asset service.
10.2 also has no /config/workloads/mssql listing (its mssql* schemas
are left over, used by no path), so SQL Server comes from the asset
service as VMs do. A workload a primary does not have answers 404 and
is skipped.

**What "protected" means for NetBackup.** An asset is *protected* when
NetBackup says something is set to back it up:

- a VM, instance or database: its asset service record lists at least
  one entry in `commonAssetAttributes.activeProtection.
  protectionDetailsList` - a protection plan it is subscribed to
  (`protectionPlanName`) or a policy that protects it (`policyName`),
  each with its `lastSuccessfulBackupTime`; or, failing that, it is
  named as a client of an *active* policy of its type (a VMware policy
  that lists the VM, an MS-SQL-Server policy that lists the instance or
  the database);
- a host: it is a client of at least one *active* host policy -
  Standard, MS-Windows, FlashBackup, NDMP, ... - the ones that back up
  the host itself (blt.netbackup.schemas.policy_family). Being in an
  SQL Server or VM policy says nothing about its file systems.

*Unprotected* is everything else NetBackup knows of: a VM vCenter
discovery found that no plan or policy covers, a database on a
registered instance in no policy, a host with a certificate and no
policy, a client only in deactivated policies. *Excluded* is only
`tempdb`, which SQL Server itself cannot back up. A SQL Server instance
protected database by database rather than as a whole has no protection
of its own (null: its databases carry the verdicts).

A VMware Intelligent Policy picks its VMs by a query blt does not
evaluate: such a VM counts as protected only through its asset record's
activeProtection (which NetBackup fills in for the policy that backs it
up). Whether its backups are recent is the reader's question
(blt.api.assets): the asset service's lastSuccessfulBackupTime where it
gives one, else the newest successful backup job blt holds for it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from netbackup_sdk import NetBackupClient
from sdk_primer import NotFoundError, SDKError

from .schemas import (
    NetBackupObjectIn,
    NetBackupProtection,
    NetBackupRunFinish,
    NetBackupRunOut,
    NetBackupRunStart,
    policy_family,
)
from .store import NetBackupStore

PAGE = 100  # the asset service's largest page
BATCH = 500  # objects per POST to blt
# The asset service's hypervisor workloads blt reads, and what each is
# called. Others answer 404 on a primary without them, and are skipped.
HYPERVISORS = {"vmware": "VMware", "rhv": "RHV"}
MSSQL = "mssql"
SQL_SERVER = "SQL Server"
# SQL Server cannot back tempdb up; NetBackup skips it.
_NEVER_BACKED_UP = {"tempdb"}


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip() and value.strip() not in ("None", "null"):
        return value.strip()
    return None


def _first(attributes: dict[str, Any], *names: str) -> str | None:
    for name in names:
        found = _text(attributes.get(name))
        if found:
            return found
    return None


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _why(error: Exception) -> str:
    code = getattr(error, "status_code", None)
    return f"{code}: {error}" if code else str(error)


@dataclass
class _Active:
    """What an asset's activeProtection says."""

    plans: list[str]
    last_backup_at: datetime | None
    job_id: int | None
    details: list[dict[str, Any]]


def active_protection(attributes: dict[str, Any]) -> _Active:
    """The plans and policies an asset service record says protect it,
    and the newest last successful backup among them."""
    common = attributes.get("commonAssetAttributes") or {}
    details = [
        d
        for d in ((common.get("activeProtection") or {}).get("protectionDetailsList") or [])
        if isinstance(d, dict)
    ]
    plans: list[str] = []
    last: datetime | None = None
    job_id: int | None = None
    for detail in details:
        name = _text(detail.get("protectionPlanName")) or _text(detail.get("policyName"))
        if name and name not in plans:
            plans.append(name)
        when = _time(detail.get("lastSuccessfulBackupTime"))
        if when is not None and (last is None or when > last):
            last = when
            job = detail.get("jobId")
            job_id = job if isinstance(job, int) and not isinstance(job, bool) else None
    return _Active(plans, last, job_id, details)


@dataclass
class Policy:
    """A policy, as far as the inventory needs it."""

    name: str
    policy_type: str | None
    active: bool
    clients: list[dict[str, Any]]


def read_policies(nbu: NetBackupClient, problems: list[str]) -> list[Policy]:
    """Every host, VM and SQL Server policy with its clients: the list,
    then each of those in full (the list leaves the clients out)."""
    try:
        listed = nbu.policies.list()
    except SDKError as error:
        problems.append(f"policies: {_why(error)}")
        return []
    found = []
    for item in listed:
        summary = item.get("policy") or {}
        name = _text(summary.get("policyName")) or _text(item.get("id"))
        if not name or policy_family(summary.get("policyType")) == "other":
            continue
        try:
            full = nbu.policies.get(name)
        except SDKError as error:
            problems.append(f"policy {name}: {_why(error)}")
            continue
        if full is None:  # deleted since it was listed
            continue
        policy = full.get("policy") or {}
        attributes = policy.get("policyAttributes") or summary.get("policyAttributes") or {}
        found.append(
            Policy(
                name=name,
                policy_type=policy.get("policyType") or summary.get("policyType"),
                active=attributes.get("active") is not False,
                clients=[c for c in policy.get("clients") or [] if isinstance(c, dict)],
            )
        )
    return found


def _assets(nbu: NetBackupClient, workload: str, problems: list[str]) -> list[dict[str, Any]]:
    """Every asset of one workload, a page at a time. A workload this
    primary does not have (404) is no assets, not a problem."""
    found: list[dict[str, Any]] = []
    offset = 0
    while True:
        try:
            answer = (
                nbu.raw.assetservice.list_workload_assets(
                    workload, page_offset=offset, page_limit=PAGE
                )
                or {}
            )
        except NotFoundError:
            if offset == 0:
                logger.info("Inventory: no {} workload on this primary", workload)
            else:
                problems.append(f"{workload} assets: page at {offset} not found")
            return found
        except SDKError as error:
            problems.append(f"{workload} assets: {_why(error)}")
            return found
        data = [d for d in answer.get("data") or [] if isinstance(d, dict)]
        found.extend(data)
        pagination = (answer.get("meta") or {}).get("pagination") or {}
        if len(data) < PAGE or pagination.get("hasNext") is False:
            return found
        offset += PAGE


@dataclass
class _Host:
    name: str
    record: dict[str, Any] | None = None
    policies: set[str] = field(default_factory=set)
    policy_types: set[str] = field(default_factory=set)
    inactive_policies: set[str] = field(default_factory=set)
    known_from: set[str] = field(default_factory=set)


class _Hosts:
    """Hosts by NetBackup's own key for a client, its name (lower case),
    with every name /config/hosts maps to it (hostMappings) leading to
    the same host."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.by_key: dict[str, _Host] = {}
        self._alias: dict[str, str] = {}
        for record in records:
            name = _text(record.get("hostName"))
            if not name:
                continue
            key = name.lower()
            host = self.by_key.setdefault(key, _Host(name=name))
            host.record = record
            host.known_from.add("hosts")
            for mapping in record.get("hostMappings") or []:
                alias = _text((mapping or {}).get("mappedHostName"))
                if alias and alias.lower() not in self.by_key:
                    self._alias.setdefault(alias.lower(), key)

    def key(self, name: str) -> str:
        return self._alias.get(name.lower(), name.lower())

    def add(self, name: str, known_from: str) -> _Host:
        key = self.key(name)
        host = self.by_key.setdefault(key, _Host(name=name))
        host.known_from.add(known_from)
        return host

    def aliases(self, key: str) -> list[str]:
        return sorted(alias for alias, to in self._alias.items() if to == key)


def _host_row(key: str, host: _Host, aliases: list[str]) -> NetBackupObjectIn:
    record = host.record or {}
    protection: NetBackupProtection = "protected" if host.policies else "unprotected"
    return NetBackupObjectIn(
        kind="host",
        source_key=key,
        name=_text(record.get("hostName")) or host.name,
        app_type=", ".join(sorted(host.policy_types)) or None,
        plan=", ".join(sorted(host.policies)) or None,
        protection=protection,
        attributes={
            "identifiers": {
                "host_name": _text(record.get("hostName")) or host.name,
                "aliases": aliases,
                "host_uuid": _text(record.get("uuid")),
            },
            "policies": sorted(host.policies),
            "inactive_policies": sorted(host.inactive_policies),
            "protection_from": "policy" if host.policies else None,
            "known_from": sorted(host.known_from),
            "nbu_host_type": record.get("nbuHostType"),
            "os_type": record.get("osType"),
            "os_version": record.get("osVersion"),
            "machine_type": record.get("machineType"),
            "hypervisor_type": record.get("hypervisorType"),
            "nbu_version": record.get("nbuVersion"),
        },
    )


def _related_id(item: dict[str, Any], word: str) -> str | None:
    """The id of a related asset whose relationship's name or type has
    `word` in it (an instance, say), where the record carries one."""
    for name, relation in (item.get("relationships") or {}).items():
        data = (relation or {}).get("data")
        if isinstance(data, dict) and (
            word in name.lower() or word in str(data.get("type", "")).lower()
        ):
            return _text(data.get("id"))
    return None


def _sql_host(attributes: dict[str, Any]) -> str | None:
    return _first(attributes, "clientName", "hostName", "host", "serverName", "clusterName")


def _protected_by_policy(
    names: set[str], policies: list[Policy], family: str
) -> tuple[list[str], list[str]]:
    """(active, inactive) policies of a family naming any of `names` as a
    client (its hostName)."""
    active: list[str] = []
    inactive: list[str] = []
    for policy in policies:
        if policy_family(policy.policy_type) != family:
            continue
        for client in policy.clients:
            if (_text(client.get("hostName")) or "").lower() in names:
                (active if policy.active else inactive).append(policy.name)
                break
    return sorted(set(active)), sorted(set(inactive))


def _vm_row(item: dict[str, Any], app_type: str, policies: list[Policy]) -> NetBackupObjectIn:
    attributes = item.get("attributes") or {}
    common = attributes.get("commonAssetAttributes") or {}
    name = _text(common.get("displayName")) or _text(attributes.get("displayName")) or item["id"]
    identifiers = {
        "display_name": name,
        "instance_uuid": _text(attributes.get("instanceUuid")),
        # VMware's `uuid` is the BIOS UUID (what the guest's SMBIOS shows).
        "bios_uuid": _text(attributes.get("uuid")) or _text(attributes.get("biosUuid")),
        "host_name": _text(attributes.get("hostName")),
        "dns_name": _text(attributes.get("dnsName")),
        "ip_address": _text(attributes.get("ipAddress")),
        "provider_id": _text(common.get("providerGeneratedId")),
        "mor": _text(attributes.get("mor")),
    }
    active = active_protection(attributes)
    plans, by = active.plans, "asset service" if active.plans else None
    inactive: list[str] = []
    if not plans:
        names = {
            v.lower()
            for k, v in identifiers.items()
            if v and k in ("display_name", "host_name", "dns_name", "instance_uuid", "bios_uuid")
        }
        plans, inactive = _protected_by_policy(names, policies, "vm")
        by = "policy" if plans else None
    vcenter = _text(attributes.get("vCenter"))
    capabilities = common.get("protectionCapabilities") or {}
    protection: NetBackupProtection = "protected" if plans else "unprotected"
    return NetBackupObjectIn(
        kind="vm",
        source_key=str(item["id"]),
        name=name,
        app_type=_text(common.get("workloadType")) or app_type,
        location=" / ".join(
            p
            for p in (
                vcenter,
                _text(attributes.get("datacenter")),
                _text(attributes.get("cluster")),
            )
            if p
        )
        or None,
        plan=", ".join(plans) or None,
        protection=protection,
        last_backup_at=active.last_backup_at,
        last_backup_job_id=active.job_id,
        attributes={
            "identifiers": identifiers,
            "protection_from": by,
            "protection_details": active.details,
            "inactive_policies": inactive,
            "is_protectable": capabilities.get("isProtectable"),
            "is_protectable_reason": capabilities.get("isProtectableReason"),
            "power_state": attributes.get("powerState"),
            "template": attributes.get("template"),
            "guest_id": attributes.get("guestId"),
            "vcenter": vcenter,
            "esxi_host": _text(attributes.get("host")),
            "vm_folder": attributes.get("vmFolder"),
            "last_discovered": (common.get("detection") or {}).get("lastDiscoveredTime"),
            "tags": attributes.get("tag"),
        },
    )


def _is_vm(item: dict[str, Any]) -> bool:
    kind = str((item.get("attributes") or {}).get("assetType") or "").lower()
    return kind == "vm" or "virtualmachine" in kind.replace("_", "")


def iter_netbackup_inventory(
    nbu: NetBackupClient, problems: list[str] | None = None
) -> Iterator[list[NetBackupObjectIn]]:
    """Hosts, then SQL Server instances, then their databases, then VMs:
    parents first. An area NetBackup refuses (a key without the right)
    is named in `problems` and the rest is still read; the caller decides
    what an incomplete inventory may conclude."""
    if problems is None:
        problems = []
    policies = read_policies(nbu, problems)
    try:
        records = nbu.hosts.list()
    except SDKError as error:
        problems.append(f"hosts: {_why(error)}")
        records = []
    hosts = _Hosts(records)
    for policy in policies:
        family = policy_family(policy.policy_type)
        if family not in ("host", "mssql"):
            continue
        for client in policy.clients:
            name = _text(client.get("hostName"))
            if not name:
                continue
            host = hosts.add(name, f"{family} policy")
            if family != "host":
                continue
            if policy.active:
                host.policies.add(policy.name)
                if policy.policy_type:
                    host.policy_types.add(policy.policy_type)
            else:
                host.inactive_policies.add(policy.name)

    sql = _assets(nbu, MSSQL, problems)
    instances = [
        a for a in sql if str((a.get("attributes") or {}).get("assetType")).upper() == "INSTANCE"
    ]
    databases = [
        a for a in sql if str((a.get("attributes") or {}).get("assetType")).upper() == "DATABASE"
    ]
    others = len(sql) - len(instances) - len(databases)
    if others:
        logger.info("Inventory: {} other SQL Server assets (availability groups, ...)", others)
    for asset in instances:
        name = _sql_host(asset.get("attributes") or {})
        if name:
            hosts.add(name, "mssql")

    yield [_host_row(key, host, hosts.aliases(key)) for key, host in sorted(hosts.by_key.items())]

    # SQL Server policies: (host, instance) -> policies covering the whole
    # instance, (host, instance, database) -> those naming the database.
    whole: dict[tuple[str, str], set[str]] = {}
    named: dict[tuple[str, str, str], set[str]] = {}
    for policy in policies:
        if policy_family(policy.policy_type) != "mssql" or not policy.active:
            continue
        for client in policy.clients:
            host_name, instance_name = (
                _text(client.get("hostName")),
                _text(client.get("instanceName")),
            )
            if not host_name or not instance_name:
                continue
            key = (hosts.key(host_name), instance_name.lower())
            database = _text(client.get("databaseName"))
            if database:
                named.setdefault((*key, database.lower()), set()).add(policy.name)
            else:
                whole.setdefault(key, set()).add(policy.name)

    instance_rows: dict[str, NetBackupObjectIn] = {}
    by_name: dict[tuple[str, str], str] = {}
    for item in instances:
        attributes = item.get("attributes") or {}
        common = attributes.get("commonAssetAttributes") or {}
        host_name = _sql_host(attributes)
        name = _first(attributes, "instanceName") or _text(common.get("displayName")) or item["id"]
        host_key = hosts.key(host_name) if host_name else None
        active = active_protection(attributes)
        policy_plans = sorted(whole.get((host_key or "", name.lower()), set()))
        plans = active.plans or policy_plans
        row = NetBackupObjectIn(
            kind="instance",
            source_key=str(item["id"]),
            name=name,
            parent_kind="host" if host_key else None,
            parent_key=host_key,
            app_type=SQL_SERVER,
            location=host_name,
            plan=", ".join(plans) or None,
            protection="protected" if plans else None,
            last_backup_at=active.last_backup_at,
            last_backup_job_id=active.job_id,
            attributes={
                "identifiers": {"instance_name": name, "host_name": host_name},
                "protection_from": (
                    "asset service" if active.plans else "policy" if policy_plans else None
                ),
                "protection_details": active.details,
                "state": _first(attributes, "instanceState", "state"),
                "cluster_type": attributes.get("clusterType"),
                "version": attributes.get("version"),
                "provider_id": _text(common.get("providerGeneratedId")),
            },
        )
        instance_rows[row.source_key] = row
        if host_key:
            by_name[(host_key, name.lower())] = row.source_key

    database_rows = []
    for item in databases:
        attributes = item.get("attributes") or {}
        common = attributes.get("commonAssetAttributes") or {}
        host_name = _sql_host(attributes)
        host_key = hosts.key(host_name) if host_name else ""
        instance_name = _first(attributes, "instanceName")
        name = _first(attributes, "databaseName") or _text(common.get("displayName")) or item["id"]
        parent = _related_id(item, "instance")
        if parent not in instance_rows:
            parent = by_name.get((host_key, (instance_name or "").lower()))
        instance = instance_rows.get(parent or "")
        if instance is not None:
            instance_name = instance_name or instance.name
        active = active_protection(attributes)
        plans, by = active.plans, "asset service" if active.plans else None
        if not plans:
            key = (host_key, (instance_name or "").lower())
            plans = sorted(named.get((*key, name.lower()), set()) | whole.get(key, set()))
            by = "policy" if plans else None
        if not plans and instance is not None and instance.protection == "protected":
            # Protected as part of its instance: the instance's plan.
            plans, by = (instance.plan or "").split(", "), "instance"
        protection: NetBackupProtection = (
            "protected"
            if plans
            else "excluded"
            if name.lower() in _NEVER_BACKED_UP
            else "unprotected"
        )
        database_rows.append(
            NetBackupObjectIn(
                kind="database",
                source_key=str(item["id"]),
                name=name,
                parent_kind="instance" if instance is not None else None,
                parent_key=instance.source_key if instance is not None else None,
                app_type=SQL_SERVER,
                location=" / ".join(p for p in (host_name, instance_name) if p) or None,
                plan=", ".join(p for p in plans if p) or None,
                protection=protection,
                last_backup_at=active.last_backup_at,
                last_backup_job_id=active.job_id,
                attributes={
                    "identifiers": {
                        "database_name": name,
                        "instance_name": instance_name,
                        "host_name": host_name,
                    },
                    "protection_from": by,
                    "protection_details": active.details,
                    "state": _first(attributes, "databaseState", "state"),
                    "recovery_model": attributes.get("recoveryModel"),
                    "availability_group": _first(attributes, "availabilityGroupName"),
                    "provider_id": _text(common.get("providerGeneratedId")),
                },
            )
        )

    # An instance protected database by database has no protection of
    # its own; one with nothing protected in it at all is unprotected.
    covered = {d.parent_key for d in database_rows if d.protection == "protected"}
    for row in instance_rows.values():
        if row.protection is None and row.source_key not in covered:
            row.protection = "unprotected"
    yield list(instance_rows.values())
    yield database_rows

    for workload, app_type in HYPERVISORS.items():
        vms = [
            _vm_row(a, app_type, policies) for a in _assets(nbu, workload, problems) if _is_vm(a)
        ]
        if vms:
            logger.info("Inventory: {} {} VMs", len(vms), app_type)
        yield vms


def _now() -> datetime:
    return datetime.now(UTC)


def run_netbackup_inventory(
    batches: Callable[[], Iterator[list[NetBackupObjectIn]]],
    store: NetBackupStore,
    *,
    problems: list[str] | None = None,
    now: Callable[[], datetime] = _now,
) -> NetBackupRunOut:
    """What the primary says exists, stored as an `inventory` run - which
    never moves the job watermark.

    When it succeeds the API marks everything it did not list as no
    longer present. A run that stops part way, or had to skip something
    (`problems`), keeps what it stored but is recorded as failed, and
    marks nothing: it saw less for a reason other than things going away.
    """
    run = store.start_run(NetBackupRunStart(mode="inventory", started_at=now(), lookup_seconds=0))
    logger.info("NetBackup inventory run {} started", run.id)
    collected = 0
    try:
        for batch in batches():
            for start in range(0, len(batch), BATCH):
                store.upsert_objects(run.id, now(), batch[start : start + BATCH])
            if batch:
                collected += len(batch)
                logger.info("Inventory: stored {} {}(s)", len(batch), batch[0].kind)
    except BaseException as exc:
        store.finish_run(
            run.id,
            NetBackupRunFinish(
                status="failed",
                finished_at=now(),
                jobs_collected=collected,
                error=f"{type(exc).__name__}: {exc}",
            ),
        )
        raise

    if problems:
        for problem in problems[:20]:
            logger.error("Inventory could not read {}", problem)
        summary = f"{len(problems)} could not be read, e.g. {problems[0]}"
        finished = store.finish_run(
            run.id,
            NetBackupRunFinish(
                status="failed", finished_at=now(), jobs_collected=collected, error=summary[:1000]
            ),
        )
        logger.error(
            "NetBackup inventory run {} stored {} objects but is INCOMPLETE ({} skipped): "
            "nothing has been marked as gone.",
            run.id,
            collected,
            len(problems),
        )
        return finished

    finished = store.finish_run(
        run.id,
        NetBackupRunFinish(status="succeeded", finished_at=now(), jobs_collected=collected),
    )
    logger.info("NetBackup inventory run {} succeeded: {} objects", run.id, collected)
    return finished
