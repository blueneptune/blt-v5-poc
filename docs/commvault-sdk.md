# The Commvault SDK inside blt

`src/blt/commvault/` is a small SDK for Commvault's REST API, written as
a facade: one object per CommServe connection, with a named group of
calls for each kind of thing.

```python
from blt.commvault import CommvaultClient, TokenSet

with CommvaultClient(url, access_token=TokenSet(access, refresh)) as cv:
    cv.commcell.info()["csVersionInfo"]
    for page in cv.jobs.iter_pages(lookup_seconds=3600):
        ...
    for client in cv.clients.list():
        for instance in cv.instances.list(client["clientId"]):
            ...
```

## Read-only unless you say otherwise

Every call is one of three kinds, and the SDK treats them differently:

| Kind | Examples | Allowed by default |
|---|---|---|
| A GET | `clients.list()`, `sql.databases()` | yes |
| A query sent as a POST (Commvault puts some filters in the body) | `jobs.iter_pages()`, `jobs.details()` | yes |
| A change to the CommServe | `subclients.backup()`, `jobs.kill()`, `clients.delete()` | **no** |

A change raises `ChangesNotAllowed` before any request is sent, unless
the client was built with `allow_changes=True`. blt's collector never
passes that, so nothing it does - and no bug in it - can alter a
production CommServe.

## What is in it

The endpoint is given so each call can be matched against Commvault's
API documentation. "Proven" means that request has been run against a
real CommServe (11 SP46).

### `cv.commcell`

| Call | Endpoint | |
|---|---|---|
| `info()` | `GET /CommServ` | proven |
| `packages()` | `GET /V4/commcell/available-packages` | proven |
| `install_authcode()` *change* | `POST /Organization/0/Authtoken` | proven |

### `cv.jobs`

| Call | Endpoint | |
|---|---|---|
| `iter_pages(lookup_seconds, ended_between=)` | `POST /Jobs` | proven, at 60,000+ jobs |
| `active()` | `POST /Jobs` (category 1) | proven |
| `oldest_start(lookup_seconds)` | `POST /Jobs` (limit 1) | proven |
| `get(job_id)` | `GET /Job/{id}` | proven |
| `details(job_id)`, `counts(job_id)` | `POST /JobDetails` | proven |
| `suspend`, `resume`, `kill` *change* | `POST /Job/{id}/action/pause\|resume\|kill` | proven |

### `cv.clients`, `cv.instances`

| Call | Endpoint | |
|---|---|---|
| `clients.list()`, `clients.find(name)` | `GET /Client` | proven |
| `clients.agents(client_id)` | `GET /Agent?clientId=` | proven |
| `clients.delete(client_id)` *change* | `DELETE /Client/{id}?forceDelete=1` | written, **never run** |
| `instances.list(client_id)` | `GET /Instance?clientId=` | proven |
| `instances.get(instance_id)` | `GET /Instance/{id}` | proven |
| `instances.update(instance, properties)` *change* | `POST /Instance/{id}` | proven |

### `cv.subclients`

| Call | Endpoint | |
|---|---|---|
| `list(client_id)` | `GET /Subclient?clientId=` | proven |
| `get(subclient_id)` | `GET /Subclient/{id}` | proven |
| `create(...)` *change* | `POST /Subclient` | proven |
| `set_plan(subclient_id, plan)` *change* | `POST /Subclient/{id}` | proven |
| `backup(subclient_id, level)` *change* | `POST /Subclient/{id}/action/backup` | proven |
| `delete(subclient_id)` *change* | `DELETE /Subclient/{id}` | written, **never run** |

### `cv.sql`

| Call | Endpoint | |
|---|---|---|
| `databases(instance_id)` | `GET /sql/databases?instance=` | proven |
| `discovered(subclient_properties)` | (reads `subclients.get()`) | proven |
| `set_instance_credential(instance, name)` *change* | `POST /Instance/{id}` | proven |

### `cv.plans`, `cv.storage`, `cv.credentials`

| Call | Endpoint | |
|---|---|---|
| `plans.list()`, `plans.ids()` | `GET /V4/Plan/Summary` | proven |
| `storage.disk()` | `GET /V4/Storage/Disk` | proven |
| `credentials.names()` | `GET /CommCell/Credentials` | proven |
| `credentials.create(...)` *change* | `POST /Commcell/Credentials` | proven |

Authentication (`POST /Login`, `POST /V4/AccessToken/Renew`) is handled
underneath, in `auth.py` and `client.py`.

**How far "proven" goes.** Each request was proven by the code it was
lifted from: the collector for jobs and inventory, the lab scripts for
everything else. The collector now goes through the SDK. The lab scripts
do not yet - they still make their own calls - so for the plans, storage,
packages, credentials and every *change* call, the SDK's version sends
the same request, checked by unit tests, but has not itself been run
against a CommServe.

## What it returns

Commvault's own JSON for each thing, as dictionaries, with the envelope
removed (`clients.list()` gives the list of client entities, not the
`clientProperties` wrapper). Jobs are the exception: they come back as
blt's typed `JobIn`. Typed models for the rest are worth adding once it
is clear which fields matter.

## Errors

- HTTP failures raise sdk-primer's typed errors (`AuthenticationError`,
  `NotFoundError`, `ServerError`, ...), all `SDKError`.
- Commvault reports many failures as a 200 with an error in the body. A
  changing call checks for that and raises `CommvaultError`.
- An HTML "Scheduled Maintenance" page with a 200 raises `ServerError`.
- A listing that answers 404 for "there are none" returns an empty list.

## How it is built

```
client.py      CommvaultClient: connection, auth, and the groups
_base.py       Resource (get / list / query / change), error handling
auth.py        access-token header and renewal
jobs.py  clients.py  subclients.py  sql.py  commcell.py   the groups
models.py      Commvault's jobSummary -> JobIn
inventory.py   uses the SDK to build blt's inventory (not part of it)
```

It sits on `sdk-primer-core` for transport - retries, typed exceptions,
request logging - but does not use its `BaseAPIModel` / `ResourceManager`.
Those model a resource you save, load and find at a path; Commvault's
API is not shaped that way, so each group is written against the
transport directly and shares Commvault's conventions through
`_base.Resource`.

## Adding an endpoint

1. Find the request in Commvault's API documentation, and in cvpysdk if
   the documentation is thin.
2. Add a method to the group it belongs to, using `_get`, `_list`,
   `_query` or `_change` - `_change` for anything that alters the
   CommServe, however harmless it looks.
3. Run it against a real CommServe before relying on it. Nearly every
   endpoint here differed from first expectations in some way: a sort
   flag that meant the opposite, an id that was not unique, a listing
   that left out system databases, a 404 for an empty list.
4. Add it to the tables above, with whether it has been run.

## What is not here, deliberately

Commvault's API has hundreds of endpoints. This covers the ones blt and
its lab have needed and tested, not the catalogue. Wrapping the rest
untested would produce an SDK that looks complete and is wrong in ways
only a live CommServe reveals; `cvpysdk` already exists for breadth.
Add groups as work calls for them - restores, schedules, alerts, storage
policies, VM and file-system content are the likely next ones.

`lab/cvlab.py` still makes its own raw calls through `cv.api`. Moving it
onto the SDK is straightforward and would exercise the changing calls
for real; it has been left until the lab scripts can be re-run to prove
nothing broke.
