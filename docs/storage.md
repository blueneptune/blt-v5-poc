# Storage monitoring

**Status: Commvault built 2026-11-05; NetBackup next, then Rubrik and
Veeam.** Step 2 of [roadmap-2026-11.md](roadmap-2026-11.md): what each
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

## The page

- Totals, and how many targets need attention: **Nearly full** at 95 %
  used or full within 14 days at its rate, **Filling** at 85 % or within
  60 days. Shown with an icon and the word, never colour alone.
- Grouped by source, by MediaAgent / media server, or by kind.
- Each target: used over time (7 days to a year) with capacity as a
  dashed line, a readout on hover, the samples as a table, its mount
  paths.

## Next

- NetBackup: primary -> media server -> storage server -> disk pool
  (AdvancedDisk, MSDP, BasicDisk, MSDP-C, Data Domain) -> storage unit,
  from `/storage/disk-pools` and `/storage/storage-units`. Needs a
  NetBackup source in blt (a Prefect block with the API key).
- Commvault mount paths as their own rows, once a call gives their
  capacity (`/Library/{id}` reports zeros on SP46).
- Rubrik clusters, Veeam repositories.
