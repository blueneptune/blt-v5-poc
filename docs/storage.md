# Storage monitoring

**Status: Commvault and NetBackup built 2026-11-05; Rubrik and Veeam
next.** Step 2 of [roadmap-2026-11.md](roadmap-2026-11.md): what each
source writes its backups to, how full it is, every hour, with trends.
The **Storage** page in the side panel.

## Data

Two tables in the `common` schema, product-neutral like `common.job`
([common-jobs.md](common-jobs.md)):

| Table | One row per | |
|---|---|---|
| `common.storage` | storage target of a source | name, kind, the product's word for its type, `hosts` (MediaAgents / media servers that write to it), `server` (storage server or library), status, `attributes`, `present`, first and last seen |
| `common.storage_sample` | target per collection | `capacity_bytes`, `used_bytes`, `free_bytes`, `logical_bytes` (before dedupe), `physical_bytes` (on disk), `dedupe_ratio` |

Kinds: `disk`, `dedupe`, `cloud`, `tape`, `cluster`, `other`. Sizes are
always bytes, whatever unit the product reports in. A target that
several MediaAgents share is **one** target with several hosts, so totals
count it once; grouped by host, the page lists it under each.

A few hundred targets sampled hourly is about two million rows a year;
nothing is pruned yet.

## API

| | |
|---|---|
| `POST /v1/sources/{type}/{name}/storage` | the collector's: one collection, every target with its numbers. `complete` (default) marks targets no longer listed as not present - their samples stay. The same `collected_at` twice stores one sample. |
| `GET /v1/storage` | every target with its latest sample and **trend**: least-squares growth of used bytes over the last 14 days (once there are 6 hours of samples), and at that rate days until free space runs out. |
| `GET /v1/storage/{id}` | one target, present or not. |
| `GET /v1/storage/{id}/samples` | its history, oldest first; `every=day` keeps the last sample of each day for long ranges. |

## Commvault

CommServe -> MediaAgent -> storage pool. `blt-collect-storage/hourly`
(:27 past each hour) starts `blt-storage-commcell/per-commcell` for each
CommCell block, with the same token, lock and concurrency slot as job
collection (`src/blt/storage/commvault.py`):

| What | From |
|---|---|
| capacity, free, size on disk (MB) | `GET /StoragePool` - every pool of every type |
| kind | `/V4/Storage/Disk`, `/Cloud`, `/Tape`, `/Local` (`DEDUPLICATION` -> dedupe) |
| hosts | `GET /V4/mediaAgent`, then each one's `/Associations` |
| dedupe savings, mount paths, plans | `GET /V4/Storage/Disk/{id}` (disk pools) |

Commvault gives dedupe *savings* (86.91 %); the ratio is
`100 / (100 - savings)` and logical bytes are size on disk times that.
Capacity Commvault does not know (`-1`, cloud) is left empty.

On cv-toaster: one dedupe pool on the CommServe's own MediaAgent, 1 TiB,
22.5 GiB on disk holding about 180 GiB (7.64:1).

## NetBackup

primary -> media server -> storage server -> disk pool -> storage unit.
NetBackup is a source in blt for storage and, since 2026-11-08, jobs
([netbackup.md](netbackup.md)):
one Prefect block per primary, of type **blt NetBackup primary** - the
address, an API key, and the primary's CA certificate when it is not
publicly signed. `blt-netbackup-collect-storage/hourly` (:27) starts
`blt-netbackup-storage-primary/per-primary` for each
(`src/blt/storage/netbackup.py`):

| What | From |
|---|---|
| capacity, free, used (bytes), storage server, volumes, water marks | `GET /storage/disk-pools` |
| media servers, cloud or not | `GET /storage/storage-units` that write to the pool |

- The **disk pool** is the target: it has the capacity, and its storage
  units are views onto it. A disk storage unit with no pool (BasicDisk)
  is a target of its own; tape units are left out for now.
- Kinds: MSDP and Data Domain are `dedupe`; MSDP on cloud storage is
  `cloud`, typed `MSDP-C`; AdvancedDisk and BasicDisk are `disk`.
- NetBackup reports storage with no fixed size (an MSDP cloud volume) as
  2^53 bytes: kept as no capacity, with its used bytes.

On eth00bck01: an MSDP pool of 7.6 TiB with 65 GiB used, and an MSDP-C
pool on S3 with 6.75 GiB used.

Adding a primary (the key from the environment, never the command line):

```
export NBU_API_KEY=...            # an API key for a user that may read storage
blt-netbackup add eth00bck01 --url https://eth00bck01:1556/netbackup --ca config/eth00bck01-ca.pem
blt-netbackup show
blt-netbackup storage eth00bck01  # one collection now, from this host
```

## The page

- Totals, and how many targets need attention: **Nearly full** at 95 %
  used or full within 14 days at its rate, **Filling** at 85 % or within
  60 days. Shown with an icon and the word, never colour alone.
- Grouped by source, by MediaAgent / media server, or by kind.
- Each target: used over time (7 days to a year) with capacity as a
  dashed line, a readout on hover, the samples as a table, its mount
  paths.

### Where NetBackup's data goes (the Sankey)

At the top of the page, once a NetBackup primary's storage is collected:
backups and copies that ended in the last 24 hours, 7 days or 30 days,
from where they came to the disk pool they landed in
(`GET /v1/storage/flows`, blt.api.storage_flows).

- Left: **Backups on <primary>** and **Copies from <pool>**. Right: each
  pool once, drawn as a meter that fills as the pool does, in its
  health's colour, with the icon, the word and when it will be full.
  So a pool filling up shows with what is filling it.
- Bands: backups, duplications (same primary) and replications (AIR, to
  another primary), each its own colour, with a legend; hover for the
  figures; **Show as a table** for the same figures as rows.
- Sizes are what the jobs say they wrote (`kilobytes_transferred`),
  before deduplication: the pressure on a pool, not what it took on disk.
  A multi-stream or VMware parent job is left out, its children counted.
- A job finds its pool through the pool's storage units. A replication
  is joined to the receiving primary's import of the same image (same
  backup id): both primaries must be collected. One whose import is not
  collected (yet) is drawn to the storage unit the job names, and counted
  under the chart.
- Pools nothing was written to in the window are listed under it.

**One primary** (the **Primary** chips, `?primary=<name>`; the API's
`primary`): that primary's copies only, in three columns - the pool each
copy was read from, the **SLP** that made it, and the pool it landed in,
on the same primary (duplication) or the one it replicates to. Pools are
meters on both sides, so a nearly full source pool shows with the SLPs
reading from it. A copy job's policy name is its SLP's name; a copy made
by hand (bpduplicate) has none and goes through **Not from an SLP**.
Backups are not in this view: pick **All** for them.
`scripts/nbu-flows-check.sh` table 5 lists the SLPs it will draw.

Where a copy read from: the storage unit the job names; else its SLP's
definition (the step writing where the copy went, and the step that one
copies from: docs/netbackup.md); else where the image's backup job
wrote. Copies none of these place are drawn from **unknown** and
counted under the chart.

**Following a path**: click a band, a pool or an SLP (or Tab to it and
press Enter) and the paths through it light up end to end - only their
share of each band - with each path, its size and jobs listed under the
chart. Click it again, the background, **Clear**, or press Escape to go
back. **Show as a table** lists every path, each with a **Highlight**
button. The API gives the paths as `paths` (end to end; one link each
in the all-primaries view).

## Next

- NetBackup MSDP dedupe ratio (not in the disk pool answer), tape.
- Commvault mount paths as their own rows, once a call gives their
  capacity (`/Library/{id}` reports zeros on SP46).
- Rubrik clusters, Veeam repositories.
