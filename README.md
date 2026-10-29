# blt - Commvault job collection

Collects job history from one or more Commvault CommCells into Postgres,
and keeps it current: after every run the database holds each job in the
state Commvault last reported for it. Rubrik Security Cloud jobs are
collected the same way, into a schema of their own - see
[docs/rubrik.md](docs/rubrik.md).

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
[Rubrik Security Cloud jobs](docs/rubrik.md) ·
[the REST API](docs/api.md) ·
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

Collectors and readers go through the API, never the database. Its
reference is generated from the code: **`/docs`** on a running API
(`http://127.0.0.1:8088/docs` under podman,
`https://api.blt.lab.ethos.home/docs` on the lab cluster), `/redoc`, and
[docs/openapi.json](docs/openapi.json). [docs/api.md](docs/api.md) covers
where to find it, signing in, and keeping it current.

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

## Words

[docs/glossary.md](docs/glossary.md) defines the words blt uses - source,
job, collection run, asset, sighting, ... - and maps each product's own
terms onto them.

## Where it is going

[docs/vision.md](docs/vision.md) records the goal - one UI over several
backup products, an asset refinery, three network zones - with open
questions and suggestions. [docs/deployment.md](docs/deployment.md) is how
it gets there: one set of images run on a podman host or on Kubernetes,
and the runbook for moving from one to the other.

## Prefect

Scheduling, retries and run history, with CommCell credentials kept in
Prefect blocks instead of `.env` files: turned on with `BLT_PREFECT=true`
in `config/blt.env`, and described in [docs/prefect.md](docs/prefect.md).
`deploy/kube/up.sh` starts the same pod from a Kubernetes manifest.

## Releases

While blt is still being built, there is no weekly release train: it
was paused on 2026-10-29, and comes back once someone besides its
developer depends on it. Work merges to `main` as soon as it is
finished and CI passes; a **release is a tag on `main`**, made when
that commit is deployed.

`main` always passes its checks, but it is not what production runs.
Production - the lab cluster, the work install - runs a tag.

### The loop for one piece of work

```
git checkout main && git pull
git checkout -b F/Add_policy_management
# small commits, each with its tests; run the checks often (see Development)
git push -u origin F/Add_policy_management     # and push often: CI runs on every push
git checkout main && git pull
git merge --no-ff F/Add_policy_management
git push
git branch -d F/Add_policy_management && git push origin --delete F/Add_policy_management
```

| Thing | Name | Example |
|---|---|---|
| Feature branch | `F/<What_it_does>` | `F/Add_policy_management` |
| Bug-fix branch | `B/<What_it_fixes>` | `B/Backfill_skips_last_slice` |
| Hotfix branch | `H/<What_it_fixes>` | `H/Token_renewal_loop` |
| Release tag, on `main` | the day it is deployed, `YYYY.MM.DD` | `2026.10.29`, then `2026.10.29.1` for a second the same day |
| Version in `pyproject.toml` | the same | `2026.10.29` |

- **Merge only once CI is green on the branch**, so `main` always passes.
- **A branch that needs another's work** is merged after it, not stacked
  on it for long: merge the first, then branch from `main`.
- **Delete a branch once it is merged**, locally and on GitHub.
  `git branch -d` refuses a branch that is not merged, which is the
  check that nothing is lost.

### Releasing: tag, then deploy

Before tagging, against a real CommServe where the change touches
Commvault: `--check`, `--probe`, a collection, a second collection (a
delta), `--inventory` and `--report`. If none is reachable, say so in
the tag message.

```
git checkout -b B/Version_2026_10_29
# version in pyproject.toml: 2026.10.29; then:
uv lock
git commit -am "Version 2026.10.29"
git checkout main && git merge --no-ff B/Version_2026_10_29 && git push
git tag -a 2026.10.29 -m "What changed, in a line or two"
git push origin 2026.10.29
```

Then deploy that tag: on the cluster, its images by that tag
(docs/deployment.md); on a single host, `git checkout 2026.10.29`.
Checking out or deploying the previous tag is the rollback.

### The SDKs

`yoki-py-commvault-sdk`, `yoki-py-rubrik-sdk`, `yoki-py-veeam-sdk` and
`sdk-primer-core` use the same branch loop and are tagged `vX.Y.Z` when
blt needs a change. blt takes one by changing the tag it pins under
`[tool.uv.sources]` in `pyproject.toml`, `uv lock`, and its tests.

**Every project pins the same `sdk-primer` tag.** uv reads each SDK's
own pin, so two SDKs on different `sdk-primer` tags cannot both be in
blt. Moving `sdk-primer` forward means a new tag of every SDK blt uses.
