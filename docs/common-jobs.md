# Common jobs: the `common` schema (design)

**Status: written 2026-10-29, reviewed 2026-11-02 (decisions below).
Steps 1-3 (`common.source`, `common.job`, `/v1/jobs`) built
2026-11-02.** It is Phase 2's data foundation ([vision.md](vision.md) section
3 and 4.4): one shape for a job from any product, so the jobs page and
reports read one table instead of learning each product. Commvault and
Rubrik are collected today; Veeam and NetBackup have SDKs not yet run
against a server, so their columns below come from their documentation
and are the least certain. Words are as in [glossary.md](glossary.md).

## What changes

```
blt database
├── commvault   commcell, collection_run, job, ...     (as today)
├── rubrik      account, collection_run, job           (as today)
├── veeam, netbackup                                   (when collected)
└── common      source   - every source, any product, with a UUID
                job      - every job, any product, in the common shape
```

- **Product tables stay the record.** Each product's `job` keeps
  everything in its own words; nothing is taken out of them.
- **`common.job` is a projection:** one row per product job, written in the
  same transaction as the product row, from a mapping function per
  product. Rewriting the mapping and re-projecting from the product
  tables is always possible, so a wrong first guess costs a re-run, not
  data.
- **Product tables point up** to `common` (`commvault.commcell.source_id`,
  `rubrik.account.source_id`); `common` never points down, except by the
  product job's key, which it holds as text.

Why `common` and not `blt`, as earlier drafts had it: blt's database
login is called `blt`, and Postgres looks first in the schema named
after the login (`search_path` is `"$user", public`). A `blt` schema
would quietly take every table created or named without a schema -
Alembic's own `alembic_version` included - away from `public`.

Why a table and not a view across the product tables: a blt job id must
be stable for links (triggered, resolves, retry of) and for anything
outside blt that refers to a job; a view cannot own an id. It also lets
the jobs page use one set of indexes.

## `common.source`

| Column | |
|---|---|
| `id` | UUID - the source's id everywhere (TODO item 12) |
| `type` | `commvault`, `rubrik`, `veeam`, `netbackup`, later `vmware`, `servicenow` |
| `name` | blt's name for it, unique per type - today's CommCell / account name, which is its Prefect block's |
| `created_at` | |

The API paths keep using the name (`/v1/sources/rubrik/work`); the id is
for links and for outside systems. `GET /v1/sources` lists them all.
`commvault.commcell` and `rubrik.account` each carry the `source_id`;
a source is made the first time a run starts for it, and the migration
made one for every CommCell and account collected before it, keeping its
`created_at`. The UUIDs are random (v4), as `source_object`'s are - the
v7 of TODO item 12 is left for when Python 3.14's `uuid.uuid7` is the
floor.

## `common.job`

| Column | Meaning | Commvault | Rubrik | Veeam | NetBackup |
|---|---|---|---|---|---|
| `id` | blt job id (UUID) | | | | |
| `source_id` | → `common.source` | | | | |
| `product_job_key` | the product's own id, as text; unique per source | job id | series id (`series_key`) | session id | job id |
| `kind` | see **Kinds** | job type / operation | activity type | session type | job type |
| `state` | see **States** | status | last activity status | state + result | state + status code |
| `product_status` | the product's own words, untouched | status | status | state / result | state / status |
| `is_active` | will it still change | from state | from state | state not Stopped | state not DONE |
| `started_at`, `ended_at` | | start / end time | start time / (none) | creation / end time | start / end time |
| `updated_at` | when the product last changed it | last update time | last updated | (none: collected at) | last update |
| `asset_name`, `asset_type` | what it ran on, in the product's words | client (+ instance) | object name / type | job's object (per task) | client |
| `protection` | what made it run | subclient + plan / storage policy | SLA domain *(not yet collected)* | backup job name | policy (+ schedule) |
| `bytes_transferred` | written to backup storage | size of media | data transferred | transferred | kilobytes × 1024 |
| `bytes_protected` | the data it covered | size of application | logical size | processed size | (none) |
| `message` | why it failed or warned, one line | pending reason / failure | failure reason / last message | result message | status text |
| `parent_id` | → `common.job`: child of | (none) | (none) | task under session | parent job id |
| `source_object_id` | → the product's `source_object` it ran on, where found | client / instance | object (`rubrik.source_object`) | | client |
| `attempt` | | attempts | attempt number | | try count |
| `first_seen_at`, `last_collected_at` | blt's own | | | | |

Asset columns are names, not links: linking a job to an asset in the
refinery ([asset-refinery.md](asset-refinery.md)) is a later column
(`asset_id`), filled once assets exist.

**As built** (`src/blt/common/mapping.py`, one function per product;
`tests/test_common_jobs.py` has a test per row of the tables here):

- Written in the same transaction as the product rows, by
  `POST .../jobs` (and Commvault's `PATCH .../jobs/{id}`), only for the
  rows that upsert actually wrote. An older batch never rolls a
  `common.job` row back, as for the product rows.
- The first `blt-migrate` after the upgrade fills `common.job` from
  everything already collected (it does so whenever `common.job` is
  empty and a product table is not). After a mapping changes,
  `blt-migrate reproject` projects every job again - same blt job ids,
  new kinds and states. Tried on a copy of the lab's data: 1,107 jobs,
  none `unknown`.
- `source_object_id`: Commvault jobs link to their **client**
  (`client_id` = the client's `source_key`; jobSummary has no instance
  id, so instances are not told apart yet); Rubrik jobs to their
  **object** (`object_id` = its RSC id or global id). Done as jobs are
  stored and again for every job of the source when an inventory run
  succeeds, so jobs collected before their object was are linked then.
- Commvault `protection` is the storage policy (the plan); Commvault
  `attempt` is not collected yet. Rubrik `ended_at` is the series' last
  update once it is no longer active - RSC gives no end time.

### Kinds

What sort of work it was. Few on purpose: the product's own word for it
stays in the product table.

| Kind | Commvault | Rubrik | Veeam | NetBackup |
|---|---|---|---|---|
| `backup` | Backup | Backup, Log Backup | backup job session | BACKUP |
| `restore` | Restore | Recovery, Download, Export, Live Mount | restore session | RESTORE |
| `copy` | Auxiliary Copy | Replication, Archive | backup copy session | DUPLICATION, REPLICATION |
| `maintenance` | Data Aging, DDB jobs | Index, Diagnostic, Configuration | health check, compact | IMAGE_CLEANUP, DBBACKUP |
| `scan` | | Classification, RadarAnalysis, THREAT_MONITORING | malware scan | |
| `other` | anything not mapped | anything not mapped | | |

`collection`, `vmware_task` and `event` (glossary) are added when blt
records its own flow runs, vCenter and ServiceNow - same table.

### States

One value, from the product's status. **Unknown statuses map to
`unknown` and count as active**, as Commvault's do today: wrongly
calling a live job finished means it is never refreshed.

| State | Active | Commvault | Rubrik | Veeam | NetBackup |
|---|---|---|---|---|---|
| `queued` | yes | Queued | Queued | | QUEUED, REQUEUED |
| `running` | yes | Running, \* Pending (stopping) | Running, Canceling | Starting, Working, Postprocessing, Stopping | ACTIVE |
| `waiting` | yes | Waiting, Pending, Suspended | | Pausing, Paused | SUSPENDED, INCOMPLETE, WAITING_FOR_RETRY |
| `succeeded` | no | Completed, Committed | Success, TaskSuccess | Stopped + Success | DONE + 0 |
| `warning` | no | Completed w/ warnings | Warning | Stopped + Warning | |
| `info` | no | | Info | | |
| `partial` | no | Completed w/ errors | PARTIAL_SUCCESS | | DONE + 1 |
| `failed` | no | Failed, Failed to Start | Failure, TaskFailure | Stopped + Failed | DONE + any other code |
| `cancelled` | no | Killed | Canceled | Stopped + None *(to check)* | DONE + 150 |
| `skipped` | no | No Run | | | |
| `missing` | no | Not found in CommCell (blt's) | | | |
| `unknown` | yes | anything else | anything else | anything else | anything else |

`info` is for activity that is neither a success nor a failure -
Rubrik's `Info` today; there for other products as they show it.

`warning` is apart from `partial` because they mean different things to
the person on call: everything was protected but something was odd,
versus some of it was not protected. The glossary's earlier list had no
`warning`; it is updated with this.

## Granularity: one product job is one blt job

Products cut work differently:

- **Commvault**: one job per subclient run; an Exchange or M365 backup is
  one job covering many mailboxes.
- **Rubrik**: one series per object per run, so an M365 backup of 1,000
  mailboxes is 1,000 jobs - the 2,000+ a day seen on 2026-10-29 are
  mostly M365.
- **Veeam**: one session per job run, with a task per VM inside.
- **NetBackup**: a parent job per policy run, a child per client or
  stream.

blt keeps each product's unit as one blt job (what the product reports,
so counts match its console), with `parent_id` where the product has one.
**Grouping is a view, not storage**: the jobs page offers "by protection,
per day" rollups (Rubrik's 1,000 mailbox jobs as one line, `998
succeeded, 2 failed`) without losing the per-object rows. Rubrik's
volume is small for Postgres - 2,000 a day is about 730,000 rows a
year; it is the *display* that needs grouping, not the database.

## API

- `GET /v1/jobs` - every product: `source_type`, `source`, `kind`,
  `state`, `active`, `since`, `protection`, `asset`, paged; newest first.
- `GET /v1/jobs/{id}` - one job, with its product detail's path
  (`/v1/sources/rubrik/work/jobs/...`).
- `GET /v1/jobs/summary` - counts by state, grouped by source,
  protection and day: the rollup above.

They sit beside `/v1/sources`, as [api.md](api.md) planned.

**As built:** the list and the summary take the same filters (each of
`source_type`, `source`, `kind`, `state` may repeat; `since`/`until` are
on `started_at`; `asset` is part of a name, any case). The list is newest
start first, with jobs not yet started on top, and returns the match
count in `X-Total-Count`. The summary groups by any of `day`, `source`,
`kind`, `protection` (`?group=` repeats; default day and source) and
returns `{total, states: {state: n}}` per group. `/v1/jobs/{id}` adds the
product's own record as `product`. Rubrik gained
`GET /v1/sources/rubrik/{name}/jobs/{series_key}` so every job has a
`product_path`. Not yet: role-scoped kinds (decision 3) - they need
sign-in first.

## Steps

Each a branch of its own, each releasable:

1. ~~**`common.source`**~~ **done 2026-11-02**, backfilled from `commvault.commcell` and
   `rubrik.account`; product tables gain `source_id`.
2. ~~**`common.job`**~~ **done 2026-11-02**, with the Commvault and
   Rubrik mappings, written on every upsert, backfilled from what is
   collected; a test per mapping row in this document.
   2a. **Status lists made complete** (see "Where the status lists come
   from"): every product value mapped and tested, `unknown` reported.
   **Rubrik done** (all 11 statuses, all 74 activity types); Commvault,
   Veeam and NetBackup to come.
3. ~~**`/v1/jobs`**, `/v1/jobs/{id}`, `/v1/jobs/summary`~~ **done
   2026-11-02**.
4. Veeam and NetBackup mappings when their collectors exist - checked
   against real servers, and this document corrected.

## Where the status lists come from

The **states** above are blt's own, few on purpose. The **product
statuses** mapped into them are not yet complete:

| Product | Source so far |
|---|---|
| Commvault | its documented status words, and what the lab and work CommCells have shown (7 so far) |
| Rubrik | **complete:** RSC's `ActivityStatusEnum` (11) and `ActivityTypeEnum` (74), read from its GraphQL schema 2026-10-28; a test checks every value maps |
| Veeam | its REST API documentation only - not yet run against a server |
| NetBackup | its API documentation; first server (10.2) reached 2026-11-02, no jobs on it yet |

So before `common.job` is relied on (step 2a below): each product's full
list from its own source - RSC's schema by introspection, the OpenAPI
specs NetBackup and Veeam servers publish, Commvault's documentation,
and the vendors themselves (the user is asking each for their default
enums); a test that every listed value maps; and a report of the raw
statuses that landed on `unknown`, so gaps show instead of hiding.

## Decisions (review, 2026-11-02)

1. **`warning` is its own state.**
2. **`info` is a state** (not active, neither success nor failure),
   mapped from Rubrik's `Info` now and open to other products.
3. **The jobs page** has a row of built-in filters at the top -
   **Backup** selected by default - that switch the view. A user with
   the right role can save filters of their own, visible only to them.
   **Some kinds are role-scoped**: blt's own `collection` runs (and
   similar internal tasks) are for engineering, not shown to operations.
   Every filter is a set of `/v1/jobs` parameters, so a saved filter is
   stored parameters; saved filters and roles come with the web UI and
   its sign-in (Zitadel).
4. **Jobs map to inventory through blt's own mapping**: within a product,
   each job gets `source_object_id`, worked out when it is stored, and
   Rubrik's SLA domain (`protection`) comes from that object. Matching
   across products is the asset refinery ([asset-refinery.md](asset-refinery.md)), later.
5. **Veeam**: the session first; per-VM tasks once a real server shows
   what they hold.

**One product job is one blt job**, confirmed; grouping is a view. The
same holds for VMware-based backups: a parent job with child jobs per VM
(Veeam's session and tasks, NetBackup's parent and children, Commvault's
VM jobs), joined by `parent_id`.

## Rubrik inventory

Collected since blt 2026.10.30.1, twice daily: every object RSC lists
(`snappableConnection`, its Object Compliance data) - VMs, hosts, file
sets, databases, Azure workloads, M365 mailboxes, OneDrives, sites and
teams - protected or not, into `rubrik.source_object`.

**Every product's inventory table follows Commvault's**: named
`<product>.source_object`, with a UUID `id`, `kind` (the type, in the
product's words), `source_key` (unique per source), `name`, `present`,
`first_seen_at`, `last_seen_at` and `last_seen_run_id`. What only that
product has (Commvault's parent links and `attributes`; Rubrik's SLA
domain, compliance, snapshot counts and sizes) stays in typed columns of
its own. A cross-product inventory view, like `common.job` for jobs, is
then a union of the shared columns. Rubrik's table was first created as
`rubrik.object` and renamed to match before anything depended on it.

That gives Rubrik jobs their `protection` (decision 4) and feeds the
refinery's sightings, as Commvault's inventory does. Rubrik clusters and
SLA domains as tables of their own are still to come.
