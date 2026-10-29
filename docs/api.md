# blt's REST API

The API is the only thing that reads or writes blt's database. Collectors
post what they collect to it, and anything that reports on blt's data
should read it from the API too. The reference documentation is generated
from the code, so it can't drift from what the API actually does.

## Where to read it

| | Where |
|---|---|
| **Interactive** (Swagger UI): try every endpoint from the browser | `/docs` on a running API |
| **Reading** (ReDoc): one long page, good for reading through | `/redoc` |
| **Machine-readable** (OpenAPI 3.1): for Postman, Insomnia, client generators | `/openapi.json`, and [openapi.json](openapi.json) in this repository |

A running API is at:

| Where blt runs | API |
|---|---|
| Lab cluster | https://api.blt.lab.ethos.home/docs |
| podman (`deploy/up.sh`) | http://127.0.0.1:8088/docs |

To try an endpoint in `/docs`, click **Authorize** (top right) and paste
the API key: `BLT_API_KEY` in `config/blt.env`, or on the cluster the
`BLT_API_KEY` in the `blt-app` Secret in the `blt` namespace. After that, each endpoint's
**Try it out** button sends the key for you. `/healthz` is the only
endpoint that doesn't need it.

The docs pages need no key to *view*. They describe the API and contain
no data.

## What's in it

| Group | Paths | For |
|---|---|---|
| commvault: collection | `/commcells/{commcell_name}/...` last-run, runs, jobs, backfill | the Commvault collector; reading jobs |
| commvault: inventory and reports | `/commcells/{commcell_name}/...` objects, validation, instance-report | inventory runs; "is everything backed up" |
| rubrik | `/rubrik/{account_name}/...` last-run, runs, jobs | the Rubrik collector; reading Rubrik jobs |
| health | `/healthz` | probes |

The overview at the top of `/docs` describes the conventions (times,
paging, errors, upserts) and the steps of one collection run.

## Keeping it current

The docs are written in the code:

- **an endpoint's** summary and group are in its `@router...` line, and
  its description is its docstring (`src/blt/api/app.py`,
  `src/blt/api/rubrik.py`);
- **a field's** description is its `Field(description=...)` in
  `src/blt/schemas.py` or `src/blt/rubrik/schemas.py`;
- **the overview** and the groups are in `src/blt/api/docs.py`.

After changing any of them, regenerate the copy in the repository:

```
uv run blt-openapi
```

CI fails if `docs/openapi.json` is out of date. It also fails if an
endpoint has no summary, description or group, or if an endpoint other
than `/healthz` doesn't ask for the key (`tests/test_api_docs.py`).
