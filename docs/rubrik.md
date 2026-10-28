# Rubrik in blt

blt collects **jobs** from Rubrik Security Cloud (RSC) - every backup,
recovery, replication and archival run, which RSC calls an *activity
series* - through the
[Rubrik SDK](https://github.com/blueneptune/yoki-py-rubrik-sdk), into
its own `rubrik` schema. Hourly on Prefect, beside Commvault.

> **Status (2026-10-28): built against Rubrik's published queries; not
> yet run against a real RSC account.** The steps below start with
> `rubrik-probe` for that reason - it checks every name blt relies on
> before anything is collected.

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

| Table | |
|---|---|
| `rubrik.account` | one row per RSC account blt collects |
| `rubrik.collection_run` | one row per run: mode, status, how many jobs, any error |
| `rubrik.job` | one row per job: object, type, status, `is_active`, start, last update, SLA, cluster |

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

Without Prefect - for a first look from a laptop with blt's API
reachable (`BLT_API_URL`, `BLT_API_KEY` in the environment or
`config/blt.env`):

```
uv run --extra prefect blt-rubrik collect acme --service-account ~/blt-reader.json
```

## Reading it

```
curl -H "X-API-Key: $BLT_API_KEY" "$BLT_API_URL/rubrik/acme/jobs?active=true"
curl -H "X-API-Key: $BLT_API_KEY" "$BLT_API_URL/rubrik/acme/jobs?status=failure&limit=20"
curl -H "X-API-Key: $BLT_API_KEY" "$BLT_API_URL/rubrik/acme/last-run"
```

`/rubrik/{account}/jobs` takes `active`, `status` (any case),
`object_name` (part of it, any case), `updated_after`, `limit`,
`offset`; newest change first.

## Not done yet

- **Run against a real account** (step 2), and correct the SDK where RSC
  differs.
- **Failure reasons.** The list query leaves out each job's event
  timeline to stay light; the message of a failed job's last event is
  the next thing to add.
- **Jobs RSC forgets.** A job blt holds as running that RSC stops
  reporting (aged out) stays "running" in blt. Commvault has a
  reconcile pass for this; Rubrik will need one if it happens.
- **Inventory** (clusters, SLA domains, protected objects) - the SDK
  already reads clusters, SLA domains and SQL Server databases.
- **The generic jobs page** (docs/vision.md 4.4) showing Commvault and
  Rubrik jobs together.
