# How blt works today

A walk through what happens, in order, when a collection runs - and where
each step lives in the code. For setup commands see the
[README](../README.md); for what is still open see [TODO](../TODO.md).

## The pieces

```
  this machine (host)                         podman pod "blt"
 ┌───────────────────────────────┐          ┌──────────────────────────────┐
 │ collect.sh <commcell>         │          │ blt-api   FastAPI + SQLModel │
 │   └─ blt-collect (uv/python)  │──HTTP───▶│   :8000  (host 127.0.0.1:8088)│
 │        │                      │ X-API-Key│        │                     │
 │ config/blt.env                │          │        ▼                     │
 │ config/<commcell>.env         │          │ blt-postgres  Postgres 17    │
 └────────┼──────────────────────┘          │   :5432  (host 127.0.0.1:5433)│
          │ HTTPS, Authtoken                │   volume blt-pgdata          │
          ▼                                 └──────────────────────────────┘
   CommServe REST API
   (one per CommCell)
```

Three things, deliberately separate:

| Piece | What it is | Knows about |
|---|---|---|
| **Collector** | A short-lived script, run per CommCell, by hand or on a schedule | Commvault's API and the blt API. Never touches the database. |
| **blt API** | A long-running FastAPI service in a container | The database. Never talks to Commvault. |
| **Postgres** | A container with a persistent volume | Nothing; only the API connects to it. |

The collector holds no state of its own. Everything it needs to remember
between runs - above all, where it left off - it asks the API for.

## One run, step by step

`./collect.sh cv-toaster`

### 1. The shell entrypoint - `collect.sh`

- Takes the CommCell name as its first argument and checks
  `config/<name>.env` exists.
- Takes a lock (`.locks/<name>.lock`). If a collection for the same
  CommCell is still running, this one exits with status 75 instead of
  piling on. Different CommCells don't block each other.
- Runs `uv run --extra collector blt-collect --commcell <name>`.

### 2. Configuration - `blt/collector/settings.py`

Two files are read, the second overriding the first:

- `config/blt.env` - shared: where the blt API is and its key.
- `config/<name>.env` - that CommCell: URL, credentials, TLS, tuning.

Pydantic validates them. A missing URL or no usable credential stops the
run here (exit 78) before anything is contacted.

### 3. Authenticating to Commvault - `blt/commvault/client.py`, `auth.py`

Two supported ways, chosen by what the `.env` file contains:

- **Access token** (`CV_ACCESS_TOKEN`, preferred): sent as
  `Authtoken: Bearer <token>` on every request. No login call.
  If `CV_REFRESH_TOKEN` is there too, the collector renews the pair
  when the access token is about to expire, or when Commvault answers
  401, and writes the new tokens back into the `.env` file
  (`blt/collector/tokens.py`). `CV_TOKEN_RENEWABLE_UNTIL` in that file is
  the date a brand-new token must be created by; runs warn a week ahead.
- **Username and password**: `POST /Login` once, with the password
  base64-encoded; the returned token goes in `Authtoken`. Logged in
  again automatically on a 401.

`./collect.sh <name> --check` does only this step, then asks the
CommServe to describe itself (`GET /CommServ`) and stops.

Transport - retries with backoff, typed errors, per-request log ids -
comes from `sdk-primer-core`'s `APIClient`.

### 4. Where did we leave off? - `GET /commcells/<name>/last-run`

The collector asks the blt API for the **watermark**: the start time of
the most recent run for this CommCell that *succeeded*.

| Watermark | Mode | Asks Commvault for |
|---|---|---|
| none (never collected) | **initial** | everything active, plus what finished in the last `CV_INITIAL_LOOKBACK_HOURS` (default 24) |
| a timestamp | **delta** | everything since the watermark, minus `CV_OVERLAP_MINUTES` (default 60) |

`--full` ignores the watermark and asks for the whole history in one
query (`CV_HISTORY_LIMIT_DAYS`, default ten years, aged jobs included) -
fine on a small CommCell, heavy on a large one.

History older than the initial window is collected separately; see
"Backfill" below.

It then records that a run has started (`POST /commcells/<name>/runs`),
which also registers the CommCell the first time it is seen.

### 5. Pass one: collect - `POST /Jobs` on the CommServe

One query, paged (`CV_PAGE_SIZE`, default 500) in job-id order. Commvault
returns:

- every job that is **active right now**, whenever it started, and
- every job that **finished** within the lookup window.

Each job's `jobSummary` is translated into blt's own shape
(`blt/commvault/models.py`) - epoch seconds become timestamps, `0`
becomes "not set" - with the original JSON kept alongside. Each page is
posted to the blt API as it arrives (`POST /commcells/<name>/jobs`).

### 6. The upsert - `blt/api/app.py`

For each job in a batch the API does one "insert, or update if it's
already there", keyed on `(commcell, job_id)`:

- New job: a row is inserted.
- Known job: its row is overwritten with the new state.

Along the way the API derives two columns from Commvault's status text
(`blt/schemas.py`): a normalised `state` (`running`, `completed`,
`failed`, ...) and `is_active` (could this job still change?). It also
stamps the row with when it was collected and by which run.

One guard: a batch collected *earlier* than what a row already holds is
not allowed to overwrite it.

### 7. Pass two: reconcile

The collector asks the API: *which jobs do you still have as active that
this run did not just send you?*
(`GET /commcells/<name>/jobs?active=true&unseen_in_run=<run id>`)

Normally none. But a job can finish and fall outside the window, or be
removed from the CommServe. For each one returned:

- `GET /Job/<id>` on the CommServe. Found: upsert whatever it is now.
- Not found: mark it `missing` (`PATCH /commcells/<name>/jobs/<id>`).

After this pass, every job the database believes is active really was
active when this run looked.

### 8. Closing the run - `POST /commcells/<name>/runs/<id>/finish`

- Both passes completed: the run is recorded as **succeeded**, and its
  start time becomes the new watermark.
- Anything failed: recorded as **failed** with the error. The watermark
  does not move, so the next run covers the same ground again. Whatever
  was already loaded stays loaded - upserts make repeating it harmless.

## Backfill - `./collect.sh <name> --backfill`

A separate mode for history older than the first run reached, built so a
long history is never one heavy pull on either side.

1. Asks the API how far back this CommCell has been collected
   (`GET /commcells/<name>/backfill`).
2. Asks the CommServe for its oldest finished job (one row), which is
   where to stop.
3. Walks backwards in time slices (`CV_BACKFILL_CHUNK_HOURS`, default
   24). Each slice is one query for *finished* jobs whose end time falls
   inside it (`endTimeRange`), posted through the same upsert.
4. Saves its position after every slice
   (`PUT /commcells/<name>/backfill`), so a failure repeats one slice at
   most.
5. Stops after `CV_BACKFILL_MAX_CHUNKS` slices (default 30), pausing
   `CV_BACKFILL_PAUSE_SECONDS` between them. Run it again to continue;
   it reports when history is complete, and after that does nothing.

It is recorded as a `backfill` run and never moves the watermark, so
deltas are unaffected; the two have separate locks and can run at the
same time. Its first slice deliberately starts a day *later* than where
ordinary runs reached, re-reading the overlap, so that a clock
difference between this machine and the CommServe cannot leave a gap.

## Inventory - `./collect.sh <name> --inventory`

Jobs say what the backup product *did*. Inventory says what it thinks
*exists*, which is what a protection check has to be measured against.

1. Lists every client (`GET /Client`) with its agents.
2. Lists every instance on each client (`GET /Instance?clientId=`).
3. For each SQL Server instance, builds its database list from two
   places that are deliberately kept apart:
   - **exists** - the content of the instance's subclients, where
     Commvault records each database its backup discovery has seen;
   - **protected** - `GET /sql/databases?instance=`, which gives a last
     backup time and job per user database.
4. Posts all of it, parents first, to `POST /commcells/<name>/objects`.
   Each thing is one row in `source_object`, keyed by the source's own
   identifier and updated in place.
5. When the run finishes successfully, the API marks every object the
   run did not see as no longer present. A failed run marks nothing.

It is an `inventory` run: its own lock, and no effect on the job
watermark.

## Validation - `./collect.sh <name> --validate`

Reads the stored inventory through `GET /commcells/<name>/validation`.
Commvault is not contacted, so it is cheap and can run as often as
wanted.

| Verdict | Means | Counts as a problem |
|---|---|---|
| `ok` | backed up within `--max-age-hours` (default 24) | |
| `stale` | backed up, but longer ago than that | yes |
| `unprotected` | Commvault lists the database and has no backup of it | yes |
| `empty` | a SQL Server instance with no databases known at all - never backed up, or the agent cannot log in | yes |
| `unverified` | `master` / `model` / `msdb`: in backup content, but Commvault reports no per-database backup time for them | |
| `gone` | was in the inventory, and the latest one no longer lists it | |

Each database row also carries its last backup job id and - if blt has
collected that job - the job's status, as a second opinion on whether
the backup that is being counted actually succeeded. Exit status is 2 if
there is any problem, 0 otherwise.

## Per-backup report - `./collect.sh <name> --report`

One line per SQL Server instance, for its most recent full backup:

```
CLIENT  INSTANCE  FULL JOB  STATUS     DISCOVERED  BACKED UP  NOTES
sql01   sql01     112       Completed  14          13         missed: hr
```

- **Discovered** - databases in the inventory for that instance.
- **Backed up** - those whose last full backup is this job, matched by
  name. Commvault's own total for the job (from job details) is stored
  beside it as a cross-check, and is what lets the three system
  databases be counted, since Commvault gives them no per-database job.
- **Notes** - the databases this full missed, anything unconfirmed, and
  the job's status if it did not simply complete.

An instance that has never had a SQL backup still gets a line - no job,
nothing discovered, a note saying so - because a report made only of
backups would leave out exactly the hosts that have none.

The live report is computed from the inventory
(`GET /commcells/<name>/instance-report`). Each successful `--inventory`
also stores it in `instance_backup_report`, one row per instance per full
backup job, so earlier backups can be queried or grouped
(`--report --history`, or SQL). That history is only as fine as the
inventory schedule: a full that came and went between two inventories
leaves no row.

Exit status 2 if any instance has a database missed or no backup at all.

### What this can and cannot tell you

Today the backup product is the only witness to what exists. That makes
the check honest about one thing and blind to another:

- It **finds** what Commvault knows about and has not protected: a
  database that fails every night, one that has gone stale, an instance
  it can no longer log in to, a database that disappeared.
- It **cannot find** what Commvault has never seen. Commvault only
  learns of a SQL Server database when a backup's discovery runs, so one
  created since the last backup is not in the inventory at all; nor is a
  server with no agent. In the lab, a database added and not yet backed
  up is `UNPROTECTED` to a check that asks SQL Server directly
  (`lab/50-sql-validate.sh`) and simply absent here.

The design leaves room for that second kind of witness without
reshaping anything: `source_object` rows belong to a source, the report
names the sources it rests on, and another source's rows - ServiceNow,
SQL Server itself - would sit beside Commvault's to be compared, not
merged into them. See docs/asset-refinery.md.

## What is in the database afterwards

| Table | One row per | Holds |
|---|---|---|
| `commcell` | CommCell | its name, and how far back its history has been backfilled |
| `collection_run` | run | mode (initial, delta, full, backfill), start/finish, lookup window, counts, status, error |
| `job` | job per CommCell | the latest state it was seen in |
| `source_object` | client / instance / database per source | name, parent, kind of application, last backup time and job, whether still present |

`job` is a picture, not a history: a job that went Queued → Running →
Completed across three runs is one row that now says Completed. Its
`first_seen_at` stays put; `last_collected_at` and `last_seen_run_id`
move with each run that sees it.

Columns worth knowing on `job`:

- `status` - Commvault's own wording. `state`, `is_active` - derived.
- `job_type`, `operation`, `backup_level`, `app_type`, `client_name`,
  `subclient_name`, `storage_policy` - what the job was.
- `start_time`, `end_time`, `elapsed_seconds`, `percent_complete`,
  `size_of_application`, `size_of_media`, `total_files`, `failed_files`.
- `raw` - Commvault's complete `jobSummary` as JSON, for anything not
  broken out into a column.

## Why it is shaped this way

- **The API owns the watermark**, so any number of collectors, on any
  hosts, agree on where each CommCell left off.
- **Everything is an upsert**, so a retried request, a re-run, or the
  overlap window can never create duplicates.
- **The overlap window** covers a job finishing right around a run, and
  clocks that disagree between this machine and the CommServe.
- **The watermark is a run's *start* time**, taken before any data is
  fetched, so it can never claim more than was actually collected.
- **"Active" means "not finished"**, not just running or queued, so a
  suspended or waiting job is still followed until it ends.

## Where each part lives

```
collect.sh                     shell entrypoint, per-CommCell lock
config/                        blt.env, <commcell>.env (gitignored), *.example
src/blt/
  schemas.py                   payloads shared by collector and API; status -> state
  commvault/
    client.py                  Commvault REST calls (jobs, clients, instances, databases)
    inventory.py               Commvault's clients/instances/databases -> ObjectIn
    auth.py                    access-token header + renewal
    models.py                  Commvault's jobSummary -> blt's JobIn
  collector/
    cli.py                     blt-collect: wires settings, clients, the run
    collect.py                 collect + reconcile, backfill, inventory
    store.py                   the collector's client for the blt API
    settings.py                config loading and validation
    tokens.py                  writing renewed tokens back to the .env file
  api/
    app.py                     FastAPI routes
    models.py                  SQLModel tables
    migrate.py                 blt-migrate: apply schema migrations
  migrations/                  Alembic migration scripts
deploy/up.sh, down.sh          build the image, start/stop the pod
Containerfile                  the API image
tests/                         mocked-Commvault and real-Postgres tests
```
