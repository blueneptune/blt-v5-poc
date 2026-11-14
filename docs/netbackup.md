# NetBackup in blt

**Status: storage since 2026-11-05 ([storage.md](storage.md)); jobs since
2026-11-08; the Sankey of backups and replication across primaries since
2026-11-13 (storage.md). Next: the image catalog with SLP state, and the
SLP backlog report.**

A NetBackup primary is one source in blt, added under **Administration
-> Sources -> Add a NetBackup primary** ([admin.md](admin.md)): one
Prefect block, of type **blt NetBackup primary**, holding the address,
an API key and the primary's CA certificate. The same block drives both
collections; **Turn on** collection on the primary's page, and both run.

## Jobs

`blt-netbackup-collect-jobs/every-15-minutes` (:02, :17, :32, :47 UTC)
starts `blt-netbackup-jobs-primary/per-primary` for each primary whose
collection is on. Each run asks the primary for every job **changed**
since the last good run (NetBackup's `lastUpdateTime`), less 30 minutes'
overlap - new jobs, jobs that moved on, jobs that finished - in one pass,
as Rubrik's collector does (`src/blt/netbackup/`).

- **The first run** reads every job changed in the last 7 days. NetBackup
  only keeps finished jobs for `KEEP_JOBS_HOURS` (78 by default), so in
  practice that is everything the primary still has. **blt keeps them
  for good.**
- **A failed run** keeps what it stored but does not move the watermark:
  the next run asks from the same point again.
- **A full run** - Administration -> Schedules,
  `blt-netbackup-jobs-primary/per-primary`, Run now with `full` on -
  reads back 30 days, for after an outage.
- Every job type is collected: backups, duplications, replications,
  imports, restores, image cleanups, catalog backups.

Where they show:

- the **Jobs** page, beside Commvault and Rubrik, in blt's common shape -
  filter **Source type** to `netbackup`;
- `GET /v1/sources/netbackup/{name}/jobs`, in NetBackup's own terms, with
  filters for type, status code, client, policy and backup id, and
  NetBackup's whole record in `raw`.

### How a NetBackup job reads on the Jobs page

| blt | From NetBackup |
|---|---|
| state | `QUEUED` queued; `ACTIVE` running; `REQUEUED`, `SUSPENDED`, `INCOMPLETE` waiting; once `DONE`, the status code: 0 succeeded, 1 partial, 150 cancelled (stopped by an administrator), 196 skipped (backup window closed), any other failed |
| kind | `jobType`: BACKUP, ARCHIVE, DBBACKUP, SNAPSHOT backup; RESTORE and instant recovery restore; DUPLICATE, REPLICATE, IMPORT, the SNAP copies and VAULT copy; IMAGEDELETE, tape and housekeeping maintenance; VERIFY, INDEXING scan |
| asset | client |
| protection | policy |
| message | the status code and its meaning, for a job that did not succeed: `status 58: can't connect to client` |
| bytes transferred | `kilobytesDataTransferred` (10.5 and later: what was actually sent), else `kilobytesTransferred` |

Every type and state in NetBackup 10.2's API is mapped (the tests check
the full list). A copy job keeps both ends - `source_media_server` /
`source_storage_unit` and `media_server` / `storage_unit` - and its
`backup_id`: what the replication Sankey will be drawn from.

Many NetBackup versions leave a copy job's source storage unit out. The
Storage page's Sankey then reads it from the copy's **SLP** (the job's
policy): blt collects every SLP (GET /config/slps) with the primary's
storage, each hour, into `netbackup.slp` - each step's storage unit and
the step it copies from (GET /v1/sources/netbackup/{name}/slps shows
them). Failing that, the storage unit the image was backed up to.

### What the API key needs

Read access to **jobs** and **storage lifecycle policies** as well as
storage. Without SLPs, storage is still collected (the run logs a
warning) and the Sankey falls back to each copy's backup job. The primary's **Check**
(Administration -> Sources -> the primary) has a `jobs` line; if it says
FAIL, add jobs (view) to the key's user's role in NetBackup (Security ->
RBAC). docs/admin.md has the `blt_r` service account steps.

### NetBackup versions

The SDK picks the API version for 10.0 (7.0), 10.2 (9.0), 10.5 (12.0) and
11.0 (13.0), and steps down by itself if a primary refuses one. Before
10.5 there is no `kilobytesDataTransferred`; blt uses
`kilobytesTransferred` there.

## From a host, without waiting for the schedule

```
blt-netbackup jobs nbu-work-01           # one job collection now
blt-netbackup jobs nbu-work-01 --full    # read back 30 days
```

It uses the primary's Prefect block, like the flows, and needs blt's API
(`BLT_API_URL`, `BLT_API_KEY`, from the environment or `config/blt.env`).
