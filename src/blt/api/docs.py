"""What the generated API docs say about the API as a whole: the
overview at the top of /docs and /redoc, and the groups the endpoints
are listed under. Each endpoint's own text is its docstring in app.py
or rubrik.py; each field's is its description in blt.schemas or
blt.rubrik.schemas.

docs/openapi.json is generated from all of it (`blt-openapi`), and a
test fails when it is out of date.
"""

from importlib.metadata import PackageNotFoundError, version
from typing import Annotated, Any

from fastapi import Path
from pydantic import BaseModel, Field

try:
    VERSION = version("blt")
except PackageNotFoundError:  # running from a bare checkout
    VERSION = "0.0.0"

TITLE = "blt API"

# Every path but /healthz is under this. See DESCRIPTION, "Versions".
API_V1 = "/v1"

DESCRIPTION = """
blt collects backup jobs and inventory from backup products (Commvault,
Rubrik) into one database. **This API is the only thing that reads or
writes that database.** Collectors never touch Postgres: they ask the API
where they left off, post what they collected, and report how the run
went. Reports and other readers use the same API.

## Signing in

Every endpoint except `/healthz` needs the API key in an `X-API-Key`
header. In these docs: **Authorize** (top right), paste the key, and the
*Try it out* buttons send it.

```
curl -H "X-API-Key: $BLT_API_KEY" \\
    https://api.blt.lab.ethos.home/v1/sources/commvault/cv-toaster/last-run
```

## Conventions

- **Sources are named in the path**:
  `/v1/sources/{source type}/{source name}/...` - a CommCell is
  `/v1/sources/commvault/cv-toaster`, an RSC account
  `/v1/sources/rubrik/work`. The name is blt's own (its Prefect
  block's), not the product's. Each source type has its own endpoints
  below that, in its product's own terms.
- **Times** are ISO 8601 with a time zone (`2026-10-29T14:00:00Z`). A
  time in a request body without one is refused.
- **Lists** page with `limit` and `offset`.
- **Errors** come back as `{"detail": "..."}`: 401 for a missing or wrong
  key, 404 for an unknown source, run or job, 422 for a body or
  parameter that does not validate.
- **Writes are upserts**, keyed by the source's own ids. Posting the same
  batch twice changes nothing; a batch collected *before* what is
  already stored is ignored, so a slow run cannot roll a job back.

## Versions

Everything except `/healthz` is under `/v1`. Changes that don't break a
client (a new endpoint, field or optional parameter) are made in `/v1`
as it stands. A change that would break one goes into a `/v2` beside
it, and `/v1` stays until every collector has moved over.

The paths from before versioning (`/commcells/{name}/...`,
`/rubrik/{name}/...`) still work, for collectors on an older release.
They answer with a `Deprecation: true` header, are not listed here, and
will be removed.

## One collection run

1. `GET .../last-run` - the watermark: where the last successful run
   started. None means never collected.
2. `POST .../runs` - open a run; keep its `id`.
3. `POST .../jobs` - the jobs collected, in batches, each with the run id.
4. `POST .../runs/{run_id}/finish` - succeeded or failed. Only a
   succeeded run moves the watermark.

Commvault runs also check on jobs still active (`GET .../jobs?active=true`)
and reconcile ones the CommCell no longer knows (`PATCH .../jobs/{job_id}`).
Rubrik runs need neither: RSC reports every change, so one pass covers it.
"""

# The groups the Commvault endpoints are listed under.
COLLECTION = "commvault: collection"
REPORTS = "commvault: inventory and reports"

TAGS = [
    {"name": "health", "description": "Is the API up, and can it reach its database."},
    {
        "name": COLLECTION,
        "description": "What the Commvault collector calls each run: watermark, runs, jobs, "
        "backfill. Readers use `GET .../jobs` too.",
    },
    {
        "name": REPORTS,
        "description": "What each CommCell says exists (clients, instances, databases), and "
        "the reports built from it: is everything it knows about backed up.",
    },
    {
        "name": "rubrik",
        "description": "Rubrik Security Cloud accounts: watermark, runs, and jobs (RSC "
        "*activity series*). See docs/rubrik.md.",
    },
]


class Problem(BaseModel):
    """An error: what went wrong, in words."""

    detail: str = Field(description="What went wrong.", examples=["Unknown commcell 'cv-nope'"])


UNAUTHORIZED: dict[int | str, dict[str, Any]] = {
    401: {"model": Problem, "description": "Missing or invalid `X-API-Key`"}
}


def not_found(what: str) -> dict[int | str, dict[str, Any]]:
    return {404: {"model": Problem, "description": f"Unknown {what}"}}


# The path parameters every endpoint shares, described once.
CommCellName = Annotated[
    str,
    Path(
        description="blt's name for the CommCell - its Prefect block's name, not necessarily "
        "the CommServe's host name. Made the first time a run is started for it.",
        examples=["cv-toaster"],
    ),
]
RubrikAccountName = Annotated[
    str,
    Path(
        description="blt's name for the RSC account - its Prefect block's name, not "
        "the account's address. Made the first time a run is started for it.",
        examples=["work"],
    ),
]
RunId = Annotated[int, Path(description="The run's `id`, as `POST .../runs` returned it.")]
CommvaultJobId = Annotated[int, Path(description="Commvault's job id.", examples=[4711])]
