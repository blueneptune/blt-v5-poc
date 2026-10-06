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

Guides: [how a run works, step by step](docs/blt-flow.md) ·
[lab noise rig for the eval CommServe (plan)](docs/lab-noise.md) ·
[open items](TODO.md)

## Setup

```
cp config/blt.env.example config/blt.env          # set BLT_API_KEY, POSTGRES_PASSWORD
./deploy/up.sh                                    # build image, start Postgres + API

cp config/commcell.env.example config/prod.env    # one file per CommCell
./collect.sh prod
```

`config/*.env` is gitignored; only the `.example` files are tracked.

## What a run does

1. **Collect.** Asks the API for the watermark - when the last successful
   run for this CommCell started.
   - No watermark: a **full** run, as far back as Commvault still has job
     history (`CV_INITIAL_LOOKBACK_DAYS`, aged jobs included).
   - Otherwise a **delta**: every job active right now, plus every job
     that finished since the watermark, less `CV_OVERLAP_MINUTES`.
2. **Reconcile.** Any job the database still has as active that step 1
   didn't return is looked up by id and stored as whatever it is now - or
   marked `missing` if the CommCell no longer has it.

Everything is written with an upsert keyed on `(commcell, job_id)`, so
there is one row per job, re-running is harmless, and a run that fails
partway keeps what it loaded but doesn't move the watermark.

```
./collect.sh prod                    # full the first time, delta after
./collect.sh prod --check            # only prove the credentials work
./collect.sh prod --full             # ignore the watermark
./collect.sh prod --log-level DEBUG  # every HTTP request
```

A second `collect.sh` for the same CommCell while one is running exits
immediately (status 75), so it is safe to schedule tightly, e.g. cron:

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
| `PATCH /jobs/{job_id}` | set one job's status (used for `missing`) |

## Database

`job` (one row per job, latest state; `raw` JSONB holds Commvault's whole
`jobSummary`), `collection_run` (one row per run), `commcell`.

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

```
uv sync --all-extras
podman exec blt-postgres psql -U blt -c 'CREATE DATABASE blt_test'   # once
BLT_TEST_DATABASE_URL=postgresql+psycopg://blt:<pw>@127.0.0.1:5433/blt_test uv run pytest
uv run ruff check . && uv run mypy
```

Without `BLT_TEST_DATABASE_URL` the database-backed tests are skipped.
