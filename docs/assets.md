# Assets

What every backup source says exists, and whether it is backed up. The
UI's **Assets** page reads it; so can anything else, at `GET /v1/assets`
and `GET /v1/assets/summary`.

Every source blt collects from must say what it knows of, not only what
it backs up. The interesting rows are the ones a source has
**discovered but does not protect**: a VM on a hypervisor it was given,
in no VM group; a database on an instance it has an agent on, in no
subclient that backs it up.

## What each source contributes

| Source | Read from | Kinds |
|---|---|---|
| Commvault | Classic API (clients, agents, instances, SQL subclient content, `/sql/databases`) plus V4 listings: `/V4/Servers` (every server, not only infrastructure), `/V4/FileServers`, `/V4/Databases` (every engine), `/V4/VirtualMachines` (protected and discovered) | host, instance, database, vm |
| Rubrik | RSC `snappableConnection`: every object, every workload, protected or not | vm, database, file_system, m365, host, other |
| NetBackup | The 10.2 asset service: `/asset-service/workloads/vmware/assets` (and `rhv`), `/asset-service/workloads/mssql/assets`; `/config/hosts`; every host, VM and SQL Server policy with its clients (`/config/policies`) | host, instance, database, vm |

Each product keeps its own inventory table, as each says it
(`commvault.source_object`, `rubrik.source_object`,
`netbackup.source_object`). `/v1/assets` reads them side by side. Nothing is merged across sources: a server that is a
Commvault client and a Rubrik VM is two rows. Linking them is the asset
refinery's job (docs/asset-refinery.md).

Every inventory runs twice a day: Commvault's at 05:37 and 17:37 UTC, Rubrik's at :47, NetBackup's (`blt-netbackup-collect-inventory/twice-daily`, one `blt-netbackup-inventory-primary/per-primary` run per primary) at :57. NetBackup's runs on its own, so an inventory that fails never holds up the primary's jobs or storage; `blt-netbackup inventory <name>` runs one by hand.

## Protection and the verdict

Each source says, of each row, whether it is **protected**,
**unprotected** or **excluded** (`protection`), and under which plan or
SLA domain (`plan`):

- **Commvault.** Excluded if its SLA says so. Unprotected if its SLA is
  "yet to be protected", or a VM's status is discovered or not
  protected, or nothing covers it (no plan, never backed up). Otherwise
  protected. A SQL database in a subclient's content is in that
  subclient's plan.
- **Rubrik.** Protected, NoSla (unprotected) or DoNotProtect (excluded).
- **NetBackup.** A VM, SQL Server instance or database is protected when
  its asset service record lists a protection plan or policy in
  `commonAssetAttributes.activeProtection.protectionDetailsList`, or,
  failing that, when an *active* policy of its type names it as a
  client. A host is protected when it is a client of an active host
  policy (Standard, MS-Windows, FlashBackup, NDMP, ...) - its SQL
  Server or VM policies say nothing of its file systems. Everything
  else it knows of is unprotected: a VM vCenter discovery found in no
  plan or policy, a database on a registered instance in no policy, a
  host with a certificate and no policy, a client only in deactivated
  policies (its `deactivated_policies` names them, and the Assets page
  marks it *in a deactivated policy*). `tempdb`, which SQL Server cannot back up, is excluded. An
  instance protected database by database has no protection of its own
  (unknown: its databases carry the verdicts). The last backup is the
  asset service's `lastSuccessfulBackupTime`; NetBackup gives none for a
  host, and none for a VM a VMware Intelligent Policy picks by query
  before its record names the policy, so for hosts and VMs blt falls
  back to the newest successful backup job it holds for that client (a
  host's own host policies only; for a VM, VM policies under its display,
  host or DNS name). `GET /v1/sources/netbackup/{name}/objects` says
  which (`last_backup_from`). Rows carry what another source would match
  them by in `attributes.identifiers`: instance and BIOS UUIDs, host and
  DNS names, a host's aliases (`hostMappings`), an instance's name.

blt adds how recent the backups are. The verdict:

| Verdict | Meaning |
|---|---|
| protected | The source protects it, and its last backup is within the window (7 days unless `stale_days` says otherwise) |
| stale | The source protects it, but has no backup that recent |
| unprotected | The source knows of it, and nothing protects it |
| excluded | Deliberately left out |
| unknown | The source does not say: a Commvault instance, a NetBackup instance protected database by database, or a row inventoried before blt recorded protection (until the next inventory) |

## What it cannot see

A source's inventory is its own discovery. What it was never pointed at
is in nobody's list here:

- a VM on a vCenter Commvault has no hypervisor for;
- a database created since the agent's last discovery;
- a host with no agent.

Closing that gap needs a witness that is not a backup product (vCenter
itself, ServiceNow, SQL Server's own `sys.databases`). That is the next
step after every backup source has an inventory.
