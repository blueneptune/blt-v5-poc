# Glossary

The words blt uses, and what each one means - in code, in the database,
in documentation and in conversation. When a product calls something by
another name, the table at the end says which blt word it maps to.

**Status: proposed (2026-10-20).** Terms marked *(today)* describe what
exists now; *(planned)* ones name things being designed, so the design
can use them from the start. Where the current code uses a different
word, the entry says so under **Today**.

## The system

**blt**
: The whole system: collection, inventory, the API and (later) the UI.
  Written in lower case.

**zone**
: A network area with its own access rules. Three kinds:
  - **customer zone** - where the systems blt collects from live
    (backup servers, vCenters). Reachable only from the proxy zone.
  - **proxy zone** - where collection runs. Can reach the customer zones
    and core.
  - **core zone** - where blt's database, API, UI and Prefect server
    run. Reaches neither of the others; everything comes to it.

**core cluster / proxy cluster**
: The Kubernetes clusters in the core and proxy zones.

## Where data comes from

**source** *(planned; today "CommCell")*
: One system blt reads from: a Commvault CommServe, a Commvault Cloud
  tenant, a NetBackup primary server, a Veeam Backup & Replication
  server, a vCenter, a ServiceNow instance. Each source has a **name**
  (unique in blt, e.g. `cv-toaster`), a **source type** and a zone.
  The API names sources this way: `/v1/sources/{source type}/{name}`.
  **Today:** the code, the `commcell` table and
  `collect.sh <commcell>` still say CommCell, from when Commvault was
  the only source type.

**source type**
: What kind of system a source is: `commvault`, `netbackup`, `veeam`,
  `vmware`, `servicenow`, `rubrik`. Also the name of that type's
  database schema and code module.

**source of truth**
: The source whose value wins for one **attribute** of an asset (owner,
  operating system, ...). Set per attribute, not per source - see
  *precedence*.

**SDK**
: A Python library that talks to one source type's API:
  `yoki-py-commvault-sdk`, `yoki-py-veeam-sdk`, ... All are built on
  `sdk-primer`, read-only unless built with `allow_changes=True`.

**change call**
: An SDK call that alters the source (starting a backup, changing a
  policy). Refused unless changes are allowed, and never retried.

**credentials**
: What blt uses to log in to a source: an access/refresh token pair, or
  a username and password. Kept in a **block** (Prefect) or a `.env`
  file (collect.sh) - never both for the same token pair.

**MSP login**
: Commvault Cloud: one partner login that reaches many customer
  tenants. A source of its own that is not collected from; customer
  sources borrow its credentials.

## Collecting

**collection** *(today)*
: Reading from a source and storing the result. Two kinds:
  - **job collection** - runs (see *job*): what is running, what has
    finished since the last time.
  - **inventory** - what exists: clients, VMs, databases, CIs.

**collection run** (or **run**) *(today: `collection_run` table)*
: One collection for one source, recorded with its mode, start, end
  and outcome. Modes:
  - **initial** - a source's first run: what is active plus the last
    24 hours.
  - **delta** - every run after that: what changed since the watermark.
  - **full** - everything the source still has.
  - **backfill** - older history, in time slices, by hand.
  - **inventory** - an inventory collection.

**watermark**
: The start time of a source's last successful job-collection run. The
  next run collects from there, so a failed run leaves no gap.

**collector**
: The code that does a collection: `blt-collect` (behind `collect.sh`)
  or a Prefect flow.

## Prefect

**flow / flow run**
: A flow is a piece of collection code Prefect can run
  (`collect_commcell`); a flow run is one execution of it.

**deployment**
: A flow made runnable by Prefect, with a name and usually a schedule
  (`blt-collect-jobs/hourly`).

**parent run / source run**
: A parent run starts one source run per source, all at once, and
  waits for them. A source run collects one source.

**block**
: Prefect's stored configuration. blt keeps one block per source,
  holding its settings and credentials.

**worker / work pool**
: A worker runs flow runs; a work pool is the queue it takes them from.
  Workers live in the proxy zone (today, also in core).

## Backups

**job** *(today, for backups; widened 2026-10-22)*
: Any unit of work with a start, an end and an outcome, from any source:
  - **backup** and **restore** jobs - what Commvault calls a job, Veeam
    a session, NetBackup a job, Rubrik an event;
  - **collection** jobs - blt's own Prefect flow runs collecting a
    source;
  - **VMware tasks** of chosen types (snapshot, migration, clone).
  - **event** jobs - blt's own work caused by another job, such as
    submitting or closing a ServiceNow event.

  blt keeps one row per job holding its **latest state**, not every
  state it passed through.
  - **product job** - the job as the source reports it, in the source
    type's own schema and words.
  - **blt job** *(planned)* - the same job in blt's common shape, with
    its **blt job id**, in the `blt` schema; what the jobs page and
    reports use.
  - **job kind** *(planned)* - `backup`, `restore`, `collection`,
    `vmware_task`, `event`, ...
  - **job link** *(planned)* - a relation from one job to another:
    **triggered** (a failed backup triggered an event job), **resolves**
    (a later success resolved a failure), **child of** (NetBackup's
    per-client jobs under a policy run), **retry of** (a retried
    collection).

**job status** *(planned for the common shape)*
: One of `running`, `succeeded`, `partial`, `failed`, `cancelled`,
  translated from the product's own status, which is kept beside it.

**protection** *(planned)*
: The fact that a source backs up an asset, and how (which policy,
  plan or subclient).

**validation** *(today)*
: Checking that what a source knows about is backed up, with a
  **verdict**: `ok`, `stale`, `unprotected`, `empty`, `unverified`,
  `gone`.

## Inventory *(planned)*

**object**
: Anything a source reports as existing: a client, a VM, a database, a
  CI. Said in a source's terms.

**sighting**
: One object as one source reports it, with its attributes - refreshed
  on every inventory collection. *(Today's nearest is the
  `source_object` table.)*

**asset**
: blt's single record for one real thing - a server, a VM, a database -
  built from every sighting matched to it. What a search for "bob"
  finds.

**match / link**
: Matching decides which sightings are the same asset; a link records
  one such decision, with the **match rule** that made it (same name,
  same VM UUID, same serial, ...) and how confident it is.

**precedence**
: Per attribute, the order in which sources are trusted
  ("operating system: VMware, then NetBackup, then Commvault").

**decision**
: A person's correction to matching: *these are the same asset* or
  *these are not*. The refinery honours decisions on every later run.

**asset history**
: Every change to an asset - first seen, matched, attribute changed,
  unmatched, merged, split, decided - with when, why and by whom.
  Assets keep history; jobs do not.

**refinery**
: The flow that turns sightings into assets: matching, applying
  precedence and decisions, writing history.

## Database

**schema**
: A Postgres namespace. One per source type (`commvault`, `netbackup`,
  ...) for that type's own data, and `blt` for the common layer:
  sources, sightings, assets, links, history, blt jobs. Product tables
  point up to `blt`; `blt` never points into a product schema.

## Releases

**release**
: A week's changes, named for the Monday they ship: `YYYY.MM.Wnn`
  (`2026.10.W04`). Branches: `release/<release>`, and
  `<release>/F|B|H/<What_it_does>` for features, bug fixes and hotfixes.
  See the README.

## Each product's words, mapped

| blt word | Commvault | NetBackup | Veeam (VBR) | VMware | ServiceNow | Rubrik |
|---|---|---|---|---|---|---|
| source | CommCell (CommServe), Commvault Cloud tenant | primary server (domain) | backup server | vCenter | instance | cluster |
| job | job | job | session | task | - | event / task |
| object (backup client) | client | client | backup object / computer | - | - | object |
| object (VM) | VM client | VM client | backup object | VM | CI (cmdb_ci_vm_instance) | VM |
| protection | subclient + plan | policy + schedule | job | - | - | SLA domain |
| restore point | job's data | backup image | restore point | snapshot (not a backup) | - | snapshot |
| credentials holder (MSP) | partner / MSP login | - | - | - | - | - |

Entries here are checked against each product's documentation when its
SDK is written; correct this table then.
