# Rubrik in blt

blt collects **jobs** from Rubrik Security Cloud (RSC) - every backup,
recovery, replication and archival run, which RSC calls an *activity
series* - through the
[Rubrik SDK](https://github.com/blueneptune/yoki-py-rubrik-sdk), into
its own `rubrik` schema. Hourly on Prefect, beside Commvault. Twice a
day it also takes an **inventory**: every object RSC knows about,
protected or not.

> **Status (2026-10-29): checked against a live RSC account** -
> `rubrik-probe` passes, and masked samples (`rubrik-sample`,
> `rubrik-mask`) parse in full. Not yet collected into blt from a live
> account.

## How it works

- One RSC account (`https://<account>.my.rubrik.com`) is one source,
  reached with one RSC **service account** - an id and a secret, which
  do not expire or need renewing. Every Rubrik cluster that reports to
  the account is covered by it.
- Each run asks RSC for every job **changed** since the last run (less
  an hour's overlap), finished or not, oldest change first. A job still
  running changes as it goes, so it is picked up again; when it
  finishes it is picked up with its result. One pass - no separate
  "check on active jobs" step as Commvault needs.
- The first run takes the last 24 hours. `--full` (or the flow's
  `full` parameter) takes everything changed within a year.
- Each job is one row in `rubrik.job`, overwritten with its latest
  state - as `job` is for Commvault. Its full RSC record is in `raw`.
- **Every activity type is collected**, not only backups. In a day of
  the first live account, over half were RSC's own work: Index,
  Diagnostic, Classification, RadarAnalysis, Configuration,
  THREAT_MONITORING, beside Backup and Replication. Readers that want
  backups ask for them (`?activity_type=Backup`).
- Besides status and times, each row has why it failed
  (`failure_reason`, `cause_error_*`), the latest event's message,
  progress as a number, attempt, whether it was run on demand, and
  sizes in bytes. RSC has **no end time**: for a finished job,
  `last_updated` is when it ended.

| Table | |
|---|---|
| `rubrik.account` | one row per RSC account blt collects |
| `rubrik.collection_run` | one row per run: mode, status, how many jobs, any error |
| `rubrik.job` | one row per job: object, type, status, `is_active`, start, last update, SLA, cluster |
| `rubrik.object` | one row per object, as of the latest inventory: type, protection, compliance, SLA domain, cluster, last snapshot, sizes, `present` |

### Inventory

- RSC's `snappableConnection`, the data behind its Object Compliance
  report: VMs, hosts, file sets, databases, Azure and AWS workloads,
  Microsoft 365 mailboxes, OneDrives, sites and teams - every type in
  one list. The first live account had well over 5,000 objects, about
  half without an SLA domain.
- **Unprotected objects are included** (`protection_status` `NoSla` or
  `DoNotProtect`), so blt can show what is *not* being backed up, not
  only what is.
- `compliance_status` is RSC's own verdict on whether the object's
  backups meet its SLA domain (`IN_COMPLIANCE`, `OUT_OF_COMPLIANCE`,
  ...); `missed_snapshots` counts the backups it should have had.
  RSC refreshes these figures on its own schedule - `pull_time` says
  when.
- `object_state` `RELIC` is RSC's word for an object gone from its
  source whose snapshots are still kept.
- An inventory run is a `collection_run` with mode `inventory`; it
  never moves the job watermark. When it succeeds, objects it did not
  list are marked `present = false` (they stay, with when they were last
  seen). A run that stops part way marks nothing gone.
- Twice a day: `blt-rubrik-collect-inventory/twice-daily`, at 05:47 and
  17:47 UTC, ten minutes after Commvault's.

## Setting up an account

**1. A read-only service account in RSC.** In RSC: Settings > Users >
Service Accounts > Create. Give it a role that can only *view* (blt
never changes anything in Rubrik). Download the JSON file it offers -
RSC shows the secret only once. Keep it out of this repository.

**2. Check RSC agrees with the SDK** - once per account, and after any
SDK upgrade. From a checkout of `yoki-py-rubrik-sdk`:

```
RUBRIK_SERVICE_ACCOUNT=~/blt-reader.json uv run rubrik-probe
```

Every line should say `ok`. A `FAIL` names the field or filter RSC does
not have and what it has instead; the output contains no object names
or secrets, so it can be pasted as it is. Fix the SDK before going on.

**3. The account's Prefect block** - named as you want the account to
appear in blt (`acme` below):

```
source scripts/use-cluster.sh            # for the cluster's Prefect
uv run --extra prefect blt-rubrik add acme ~/blt-reader.json
uv run --extra prefect blt-rubrik show acme
```

The secret goes into the block (encrypted by Prefect, shown as `set`)
and is never printed. blt does not need the file after this.

**4. First collection, by hand:**

```
uv run --extra prefect prefect deployment run 'blt-rubrik-collect-account/per-account' \
    -p account=acme --watch
```

From then on, `blt-rubrik-collect-jobs/hourly` (at 17 past each hour)
collects every account block that has `collect` on.

**5. First inventory, by hand** (or wait for the next twice-daily):

```
uv run --extra prefect prefect deployment run 'blt-rubrik-inventory-account/per-account' \
    -p account=acme --watch
```

Without Prefect - for a first look from a laptop with blt's API
reachable (`BLT_API_URL`, `BLT_API_KEY` in the environment or
`config/blt.env`):

```
uv run --extra prefect blt-rubrik collect acme --service-account ~/blt-reader.json
uv run --extra prefect blt-rubrik inventory acme --service-account ~/blt-reader.json
```

## Reading it

```
curl -H "X-API-Key: $BLT_API_KEY" "$BLT_API_URL/v1/sources/rubrik/acme/jobs?active=true"
curl -H "X-API-Key: $BLT_API_KEY" "$BLT_API_URL/v1/sources/rubrik/acme/jobs?status=failure&limit=20"
curl -H "X-API-Key: $BLT_API_KEY" "$BLT_API_URL/v1/sources/rubrik/acme/jobs?activity_type=Backup&activity_type=Replication"
curl -H "X-API-Key: $BLT_API_KEY" "$BLT_API_URL/v1/sources/rubrik/acme/last-run"
```

`/v1/sources/rubrik/{name}/jobs` takes `active`, `activity_type` (any case,
repeatable), `status` (any case),
`object_name` (part of it, any case), `updated_after`, `limit`,
`offset`; newest change first.

```
curl -H "X-API-Key: $BLT_API_KEY" "$BLT_API_URL/v1/sources/rubrik/acme/objects?protection_status=NoSla"
curl -H "X-API-Key: $BLT_API_KEY" "$BLT_API_URL/v1/sources/rubrik/acme/objects?compliance_status=OUT_OF_COMPLIANCE"
curl -H "X-API-Key: $BLT_API_KEY" "$BLT_API_URL/v1/sources/rubrik/acme/objects?object_type=O365Mailbox&object_type=O365Onedrive"
```

`/v1/sources/rubrik/{name}/objects` takes `object_type` (any case,
repeatable), `protection_status`, `compliance_status`, `sla_domain`
(all any case), `name` (part of it, any case), `present`, `limit` (up
to 5000), `offset`; by type, then name.

## Not done yet

- **Collect from a live account** (steps 3 and 4).
- **See a failure.** The first samples (a day, 64 jobs) had none, so how
  RSC fills `failure_reason` and the `cause_error_*` fields is not yet
  seen.
- **Jobs RSC forgets.** A job blt holds as running that RSC stops
  reporting (aged out) stays "running" in blt. Commvault has a
  reconcile pass for this; Rubrik will need one if it happens.
- **Inventory from a live account into blt.** The SDK's inventory query
  passed `rubrik-probe` on 2026-10-30 (5,000 objects read); blt has not
  stored one yet.
- **Clusters and SLA domains as tables of their own.** Objects carry
  their cluster and SLA domain names; the SDK can list both in full.
- **A consolidated view of Microsoft 365 jobs** (one line per mailbox,
  OneDrive or site per day) in the web UI. Every job stays stored; this
  is how they are shown, not what is kept.
- **The generic jobs page** (docs/vision.md 4.4) showing Commvault and
  Rubrik jobs together.
