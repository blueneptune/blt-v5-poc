# Common jobs: the `blt` schema (design, for review)

**Status: a design for review, written 2026-10-29. Nothing here is
built.** It is Phase 2's data foundation ([vision.md](vision.md) section
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
└── blt         source   - every source, any product, with a UUID
                job      - every job, any product, in the common shape
```

- **Product tables stay the record.** Each product's `job` keeps
  everything in its own words; nothing is taken out of them.
- **`blt.job` is a projection:** one row per product job, written in the
  same transaction as the product row, from a mapping function per
  product. Rewriting the mapping and re-projecting from the product
  tables is always possible, so a wrong first guess costs a re-run, not
  data.
- **Product tables point up** to `blt` (`commvault.commcell.source_id`,
  `rubrik.account.source_id`); `blt` never points down, except by the
  product job's key, which it holds as text.

Why a table and not a view across the product tables: a blt job id must
be stable for links (triggered, resolves, retry of) and for anything
outside blt that refers to a job; a view cannot own an id. It also lets
the jobs page use one set of indexes.

## `blt.source`

| Column | |
|---|---|
| `id` | UUID - the source's id everywhere (TODO item 12) |
| `type` | `commvault`, `rubrik`, `veeam`, `netbackup`, later `vmware`, `servicenow` |
| `name` | blt's name for it, unique per type - today's CommCell / account name, which is its Prefect block's |
| `created_at` | |

The API paths keep using the name (`/v1/sources/rubrik/work`); the id is
for links and for outside systems.

## `blt.job`

| Column | Meaning | Commvault | Rubrik | Veeam | NetBackup |
|---|---|---|---|---|---|
| `id` | blt job id (UUID) | | | | |
| `source_id` | → `blt.source` | | | | |
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
| `parent_id` | → `blt.job`: child of | (none) | (none) | task under session | parent job id |
| `attempt` | | attempts | attempt number | | try count |
| `first_seen_at`, `last_collected_at` | blt's own | | | | |

Asset columns are names, not links: linking a job to an asset in the
refinery ([asset-refinery.md](asset-refinery.md)) is a later column
(`asset_id`), filled once assets exist.

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
| `partial` | no | Completed w/ errors | PARTIAL_SUCCESS | | DONE + 1 |
| `failed` | no | Failed, Failed to Start | Failure, TaskFailure | Stopped + Failed | DONE + any other code |
| `cancelled` | no | Killed | Canceled | Stopped + None *(to check)* | DONE + 150 |
| `skipped` | no | No Run | | | |
| `missing` | no | Not found in CommCell (blt's) | | | |
| `unknown` | yes | anything else | anything else | anything else | anything else |

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

## Steps

Each a branch of its own, each releasable:

1. **`blt.source`**, backfilled from `commvault.commcell` and
   `rubrik.account`; product tables gain `source_id`.
2. **`blt.job`** with the Commvault and Rubrik mappings, written on
   every upsert, backfilled from what is collected; a test per mapping
   row in this document.
3. **`/v1/jobs`**, `/v1/jobs/{id}`, `/v1/jobs/summary`.
4. Veeam and NetBackup mappings when their collectors exist - checked
   against real servers, and this document corrected.

## Open questions

1. **`warning` as its own state** - or folded into `succeeded` with a
   flag? Proposed: its own state.
2. **Rubrik's `Info` status** - informational activity, neither success
   nor failure. Proposed: `succeeded`, and check what kinds carry it.
3. **Which kinds the jobs page shows by default.** Proposed: `backup`,
   `restore`, `copy`; `maintenance` and `scan` behind a filter - Rubrik's
   index and diagnostic series would otherwise bury the backups.
4. **Rubrik's protection (SLA domain)** is not collected per job yet; it
   needs the SDK's object or SLA calls - see Rubrik inventory below.
5. **Veeam's per-VM tasks**: store them as child jobs from the start, or
   only the session? Proposed: the session first, tasks once a real
   server shows what they hold.

## Rubrik inventory

The Rubrik SDK does not collect inventory yet. It has clusters, SLA
domains and SQL Server databases (written, not run against RSC), but not
the protected objects themselves (VMs, hosts, M365 mailboxes, sites).
That is what would give Rubrik jobs their `protection` and feed the
refinery's sightings, as Commvault's inventory does today
(`commvault.source_object`). It is a separate piece of work: RSC's
object and SLA queries in the SDK first, then a Rubrik inventory flow.
