# blt - Commvault job collection

Collects job history from one or more Commvault CommCells into Postgres,
and keeps it current: after every run the database holds each job in the
state Commvault last reported for it.

```
collect.sh <commcell>            host, by hand or on a timer
   └─ blt-collect                uv / python / pydantic / sdk-primer
        ├─ Commvault REST API    POST /Login, POST /Jobs, GET /Job/{id}
        └─ blt API               FastAPI + SQLModel      ┐ podman pod "blt"
             └─ Postgres 17      volume blt-pgdata       ┘
```

Guides: **[setting it up against a production CommCell](docs/production-setup.md)** ·
[Commvault Cloud / SaaS](docs/saas-setup.md) ·
[how a run works, step by step](docs/blt-flow.md) ·
[the Commvault SDK blt depends on](docs/commvault-sdk.md) ·
[the lab rig for the eval CommServe, in its own repository](https://github.com/blueneptune/yoki-commvault-lab) ·
[scripts for checking what was collected](scripts/README.md) ·
[open items](TODO.md) ·
[asset refinery (draft direction, not current work)](docs/asset-refinery.md)

## Setup

```
cp config/blt.env.example config/blt.env          # set BLT_API_KEY, POSTGRES_PASSWORD
./deploy/up.sh                                    # build image, start Postgres + API

cp config/commcell.env.example config/prod.env    # one file per CommCell
./collect.sh prod
```

`config/*.env` is gitignored; only the `.example` files are tracked.

That is the short version. For a real CommCell - the Commvault user and
token to create, what to run in what order, scheduling, and what to
watch - follow [docs/production-setup.md](docs/production-setup.md).
The lab rig that makes the eval CommServe produce jobs for blt to
collect lives in its own repository,
[yoki-commvault-lab](https://github.com/blueneptune/yoki-commvault-lab),
since 2026-10-14 (it was `lab/` here). Nothing in it is for production:
it creates clients and runs backups.

## What a run does

1. **Collect.** Asks the API for the watermark - when the last successful
   run for this CommCell started.
   - No watermark: an **initial** run - everything active right now plus
     what finished in the last `CV_INITIAL_LOOKBACK_HOURS` (24). Small
     on purpose, so the current picture is right within minutes.
   - Otherwise a **delta**: every job active right now, plus every job
     that finished since the watermark, less `CV_OVERLAP_MINUTES`.
2. **Reconcile.** Any job the database still has as active that step 1
   didn't return is looked up by id and stored as whatever it is now - or
   marked `missing` if the CommCell no longer has it.

Everything is written with an upsert keyed on `(commcell, job_id)`, so
there is one row per job, re-running is harmless, and a run that fails
partway keeps what it loaded but doesn't move the watermark.

```
./collect.sh prod                    # initial the first time, delta after
./collect.sh prod --backfill         # older history, a batch of slices at a time
./collect.sh prod --inventory        # what exists: clients, instances, databases
./collect.sh prod --validate         # is everything in the inventory backed up?
./collect.sh prod --report           # per SQL instance: discovered vs backed up
./collect.sh prod --check            # only prove the credentials work
./collect.sh prod --probe            # which of blt's Commvault calls work here?
./collect.sh prod --full             # the whole history in one query
./collect.sh prod --log-level DEBUG  # every HTTP request
```

**Older history** is fetched separately with `--backfill`: it walks
backwards from where the first run reached in time slices
(`CV_BACKFILL_CHUNK_HOURS`, 24), at most `CV_BACKFILL_MAX_CHUNKS` (30)
per invocation, remembers where it got to, and stops at the oldest job
the CommServe has. Run it repeatedly - by hand or on its own timer -
until it logs that history is complete. It uses its own lock, so it can
run alongside the regular collection.

**Inventory and validation.** `--inventory` records what Commvault says
exists - every client, every instance on it, and every SQL Server
database - one row each in `source_object`, updated in place; anything a
complete inventory no longer lists is marked gone. `--validate` then
reads that (it does not contact Commvault) and gives each database a
verdict - `ok`, `stale` (older than `--max-age-hours`, default 24),
`unprotected`, `unverified`, `gone` - and flags SQL instances with no
databases known at all. It exits 2 if anything is stale, unprotected or
empty. The backup product is the only witness to what exists, so this
finds what it knows about and has not protected, not what it has never
seen; see docs/blt-flow.md.

A second collection for the same CommCell while one is running exits
immediately (status 75; the lock is taken by `blt-collect` itself, so it
works on Windows and without the shell script too), so it is safe to schedule tightly, e.g. cron:

```
*/15 * * * * /path/to/blt-v5-poc/collect.sh prod >> /var/log/blt-prod.log 2>&1
```

## API

All under `/commcells/{name}`, all requiring `X-API-Key`. Interactive
docs at `http://127.0.0.1:8088/docs`.

| | |
|---|---|
| `GET /last-run` | watermark + the most recent run |
| `POST /runs`, `POST /runs/{id}/finish` | record a run starting / ending |
| `POST /jobs` | upsert a batch of jobs |
| `GET /jobs?active=true&state=&unseen_in_run=` | query stored jobs |
| `GET /jobs/{job_id}` | one job, including Commvault's raw summary |
| `POST /objects`, `GET /objects?kind=&present=` | upsert / query the inventory |
| `GET /validation?max_age_hours=` | verdict per database, from the stored inventory |
| `GET /instance-report?history=` | per SQL instance: databases discovered vs backed up by its latest full |
| `GET`, `PUT /backfill` | how far back history has been collected |
| `PATCH /jobs/{job_id}` | set one job's status (used for `missing`) |

## Database

`job` (one row per job, latest state; `raw` JSONB holds Commvault's whole
`jobSummary`), `collection_run` (one row per run), `commcell`,
`source_object` (one row per client / instance / database a source says
exists, with its last backup time where the source reports one),
`instance_backup_report` (the `--report` row for each SQL instance, kept
per full backup job).

`status` is Commvault's own string; `state` / `is_active` are normalised
from it in `blt/schemas.py`. A status that table doesn't list is stored
as `unknown` and treated as still active.

```
podman exec -it blt-postgres psql -U blt
```

Schema changes: edit `src/blt/api/models.py`, then

```
BLT_DATABASE_URL=postgresql+psycopg://blt:<pw>@127.0.0.1:5433/blt BLT_API_KEY=x \
    uv run blt-migrate revision -m "what changed"
./deploy/up.sh        # the API container applies migrations on start
```

## Development

The Commvault SDK is a separate repository, `yoki-py-commvault-sdk`,
which `uv` fetches at a pinned tag; see
[docs/commvault-sdk.md](docs/commvault-sdk.md) for working on both at
once.

```
uv sync --all-extras
podman exec blt-postgres psql -U blt -c 'CREATE DATABASE blt_test'   # once
BLT_TEST_DATABASE_URL=postgresql+psycopg://blt:<pw>@127.0.0.1:5433/blt_test uv run pytest
uv run ruff check . && uv run mypy
```

Without `BLT_TEST_DATABASE_URL` the database-backed tests are skipped.

## Releases

blt is released once a week. `main` holds only released code and is what
production runs; nothing is committed to it directly.

### The week

| Day | What happens |
|---|---|
| Monday | The previous week's release is merged to `main` and tagged. Production pulls it. The new week's release branch opens from `main`. |
| Monday - Thursday | Work happens on short branches and is merged into the week's release branch as each piece is finished. |
| Thursday, end of day | Cut-off. What is not merged waits for next week. |
| Friday | The release branch is tested as a whole (see below). No new work goes in - only fixes for what the testing finds. |
| Monday | If Friday's tests passed, release. If not, the release is held until they do, or skipped; a week without a release is fine. |

### Names

A release is named for the Monday it is released on: `YYYY.MM.Wnn`,
where `nn` counts the Mondays of that month. `2026.10.W02` was released
on Monday 12 October 2026, the second Monday of October. The work done
in the week that follows goes into `release/2026.10.W03` and is released
on Monday 19 October.

So the branch to be on is always the one named for **next** Monday.

| Thing | Name | Example |
|---|---|---|
| Release branch | `release/<release>` | `release/2026.10.W02` |
| Feature branch | `<release>/F/<What_it_does>` | `2026.10.W02/F/Add_policy_management` |
| Bug-fix branch | `<release>/B/<What_it_fixes>` | `2026.10.W02/B/Backfill_skips_last_slice` |
| Hotfix branch | `<release>/H/<What_it_fixes>` | `2026.10.W02/H/Token_renewal_loop` |
| Release tag, on `main` | `<release>` | `2026.10.W02` |
| Hotfix tag, on `main` | `<release>.<n>` | `2026.10.W02.1` |
| Version in `pyproject.toml` | the same, as numbers | `2026.10.2`, `2026.10.2.1` |

Two details behind that table:

- The release *branch* carries a `release/` prefix because git cannot
  hold a branch called `2026.10.W02` and branches called
  `2026.10.W02/F/...` at the same time. The *tag* has no such limit, so
  it is the plain name.
- Python does not accept `W02` in a package version, so `pyproject.toml`
  says `2026.10.2`. The tag is the name people use.

### The loop for one piece of work

```
git checkout release/2026.10.W02 && git pull
git checkout -b 2026.10.W02/F/Add_policy_management
# work in small commits; run the checks often (see Development)
git push -u origin 2026.10.W02/F/Add_policy_management
git checkout release/2026.10.W02
git merge --no-ff 2026.10.W02/F/Add_policy_management
git push
```

- **Feature and bug-fix branches** start from the week's release branch
  and merge back into it.
- **Hotfix branches** are for something production cannot wait until
  Monday for. They start from `main`, merge to `main` with a hotfix tag,
  and are then merged into the open release branch as well so the fix
  is not lost the following Monday.

### Friday's test

On the release branch, all of:

1. `ruff check`, `ruff format --check`, `mypy`, and the unit tests.
2. Against a real CommServe: `--check`, `--probe`, a collection, a
   second collection (a delta), `--inventory` and `--report`.

If no CommServe is reachable, step 2 has not happened, and the release
either waits or is tagged with that stated in the tag message - it does
not go to `main` described as tested.

### Releasing, on Monday

```
git checkout main && git pull
git merge --no-ff release/2026.10.W02
git tag -a 2026.10.W02 -m "..."
git push origin main 2026.10.W02
git checkout -b release/2026.10.W03 && git push -u origin release/2026.10.W03
```

Production then runs `git pull` (or `git checkout 2026.10.W02` to pin a
release; checking out the previous tag is the rollback).

### Tidying up

A branch is deleted once it has been merged, locally and on GitHub:

```
git branch -d 2026.10.W02/F/Add_policy_management
git push origin --delete 2026.10.W02/F/Add_policy_management
```

`git branch -d` refuses to delete a branch that has not been merged
into the branch you are on, which is the check that nothing is lost. A
release branch is deleted the same way after its Monday release; the
tag keeps that release reachable.

### The SDKs

`yoki-py-commvault-sdk` and `yoki-py-veeam-sdk` use the same branch loop
but are not on the weekly clock: each is tagged whenever it has a
change, and blt takes a new SDK by changing the tag it pins in
`pyproject.toml` as part of that week's release.
