# Using blt with Commvault Cloud (SaaS, formerly Metallic)

**Status: built from Commvault's documentation, not yet run against a
SaaS tenant.** The parts that differ from a CommServe you run yourself
are in place and tested as far as they can be without one - the unit
tests, and the same code paths exercised against the eval CommServe.
The first run against a real tenant is the test, and this page is
written to make that run tell us as much as possible.

## Confirmed on a real tenant

First contact was on 2026-10-13, with an MSP access token:

- The MSP access token is accepted, with the `Authorization` header.
- `--partner-accounts` works: `GET /api/v1/partner/accounts` still lists
  accounts, though Commvault's reference does not document it.
- With a customer's `CV_ACCOUNT_ID`, the job listing works (334 jobs in
  24 hours on the first tenant tried).
- `GET /CommServ` is refused with 403. `--check` therefore no longer
  asks for it on SaaS and proves the login by listing jobs.

Everything else below is still expectation until `--probe` has been run.

## What is different about SaaS

From Commvault's documentation (links at the end):

| | Customer-managed CommServe | Commvault Cloud |
|---|---|---|
| Base URL | `https://<host>/commandcenter/api` | `https://api.metallic.io` - one address for every tenant |
| Token header | `Authtoken: Bearer <token>` | `Authorization: Bearer <token>` |
| Rate limit | none documented | **1000 reads and 100 writes an hour per tenant**, shared by everything using that tenant's API; `429` beyond that |
| Certificate | often private | public; leave `CV_VERIFY_TLS=true` |

Commvault presents one API for both ("organised by workload ... for both
Software and SaaS deployment types"), so the endpoints blt calls are
expected to be the same. Expected is not confirmed: which of them a
tenant's user may actually call is the main thing the first run will
show.

## What blt does about each

- **`CV_DEPLOYMENT=saas`** in the CommCell's `.env` switches the token
  header. (The eval CommServe, software 11 SP46, accepts this header
  too, so the SaaS code path has been run for real - against software.)
- **A call budget.** With `CV_DEPLOYMENT=saas`, blt sends at most 800
  requests an hour to Commvault from one run, and waits - saying so in
  the log - if the next would go over. Change it with
  `CV_MAX_CALLS_PER_HOUR`. It counts everything: reads, logins, token
  renewals.
  - The count is per run. Two runs close together each have their own.
  - Other tools using the same tenant's API share Commvault's limit and
    are invisible to blt. Lower the number if there are any.
- **`--check` does not ask for CommServe details on SaaS** - they are
  refused - and proves the credentials by listing jobs instead. Run
  against an MSP login file, which names no customer, it fails and says
  to check a customer's file.
- **`--probe`** - see below.

## Credentials: the open question

Commvault's SaaS page says two things:

- *"Any tenant user can access Commvault Cloud APIs using username and
  password authentication."*
- *"MSP administrators can use access tokens for accessing Core and
  partner APIs."*

So it is not certain that an ordinary tenant administrator can create an
access token that works with the API; an older community thread reports
that tokens generated in the portal did not, and that `POST /Login` did.
blt supports both, so try them in this order:

1. **Access token** (preferred - no password on disk, and blt renews
   it). Create one in the SaaS Command Center under your user's *Access
   tokens*, and put the access and refresh tokens in the `.env` file.
   Commvault documents a 2-hour life, renewable while it is used at
   least every 14 days, for up to 90 days from creation.
2. **Username and password**, if tokens are refused: `CV_USERNAME` and
   `CV_PASSWORD`, with `CV_ACCESS_TOKEN` left empty. This needs an
   account **without multi-factor authentication** - with MFA the
   password has to be sent with the current one-time code appended,
   which an unattended collector cannot do. A dedicated service account
   is the usual answer.

## Managed service providers: one login, many tenants

If your account is an MSP (partner) account, your login is not inside
any one customer's tenant, and the core API - jobs, clients, everything
blt collects - needs to be told which customer each call is for.
Commvault's API FAQ:

> **lhAccountId** - "mandatory only when a partner invokes core APIs for
> one of its customers. This is not relevant to standalone customers."
>
> **tenantGuid** - "used by MSPs to identify the tenant (customer), is an
> alternative to lhAccountId and can be fetched from the company listing
> page in Command Center."
>
> **ring-id** - "required when a customer is extended to multiple rings,
> to specify the ring context (e.g., M301, M108)".

So yes, it is still needed. In blt, each customer tenant is its own
"CommCell" - its own `.env` file, its own data, its own watermark - and
all of them borrow one credentials file:

```
# config/msp.env - the MSP's own login. Not collected from directly.
CV_BASE_URL=https://api.metallic.io
CV_DEPLOYMENT=saas
CV_ACCESS_TOKEN=...
CV_REFRESH_TOKEN=...
```
```
# config/acme.env - one customer tenant
CV_CREDENTIALS=msp
CV_ACCOUNT_ID=1001          # sent as lhAccountId
```
```
# config/globex.env - another, identified the other way
CV_CREDENTIALS=msp
CV_TENANT_GUID=...          # sent as tenantGuid
# CV_RING_ID=M301           # only if the customer spans several rings
```

Then `./collect.sh acme`, `./collect.sh globex`, and so on.

- `CV_CREDENTIALS=msp` means "take the URL, deployment and tokens from
  `config/msp.env`". When the token is renewed, the new pair is written
  back to `msp.env`, so every tenant's next run has it. Runs for
  different tenants take turns renewing and pick up each other's
  renewals, so they can be scheduled independently.
- The tenant header goes on every data call. Logging in and renewing a
  token are about the MSP's own user and do not carry it.
- **The rate limit is per tenant** according to Commvault, so each
  customer has its own 1000 reads an hour. blt's budget is per run,
  which matches.

**Finding the account id.** Your earlier code got it from
`/api/v1/partner/accounts`. Commvault's current reference still has the
partner API (`GET /api/v1/partner/accounts/{accountId}`, among others)
but does not list a call that returns all accounts, so whether the
listing still exists is not settled by the documentation. blt can try
it:

```
./collect.sh msp --partner-accounts
```

If that is refused, the tenant GUID is the documented alternative and
is shown on the company listing page in Command Center.

All of this is built from the FAQ and from how your earlier code worked,
and has unit tests; **none of it has been run against a tenant.**

## Setup

One `.env` file for the tenant, as for any CommCell. The file name is
the name blt stores its data under.

```
# config/cloud.env
CV_BASE_URL=https://api.metallic.io
CV_DEPLOYMENT=saas
CV_ACCESS_TOKEN=...
CV_REFRESH_TOKEN=...
# CV_MAX_CALLS_PER_HOUR=800
```

Everything else - the database pod, `config/blt.env` - is as in
[production-setup.md](production-setup.md).

## First contact, in this order

Each step is safe to stop after, and each is cheap against the rate
limit.

**1. Check the credentials** (1 or 2 requests).

```
./collect.sh cloud --check
```

- `Authenticated ...` - go on.
- `401` / "Access denied" - the token is not accepted. Try the other
  kind of credential above.
- Anything else - send me the line.

**2. Probe** (about 20 to 60 requests).

```
./collect.sh cloud --probe
```

This tries each Commvault call blt depends on, once, stores nothing, and
prints a line per call with what it is needed for:

```
ok     job listing                    95 jobs in the last 24 h ...   [job collection]
ok     job listing by end-time range  31 jobs ended ...              [--backfill]
FAILED client listing                 ClientError: ... -> 403        [--inventory]
```

That table is the answer to "which parts of blt work on SaaS". **Send it
to me whatever it says** - it is what turns this page from expectation
into fact. Exit status is 0 if job collection will work.

**3. Collect** - only the parts the probe passed.

```
./collect.sh cloud               # active jobs + the last 24 hours
./collect.sh cloud               # a delta
./collect.sh cloud --backfill    # older history, in batches
```

A first collection costs one request per 500 jobs. To use fewer
requests on a busy tenant, raise `CV_PAGE_SIZE` (try 1000) - if the API
accepts it, that halves the count.

**4. Inventory**, if the probe passed it - and with the arithmetic done
first:

```
./collect.sh cloud --inventory
```

Inventory costs about two requests per client plus several per SQL
Server instance. Under a budget of 800 an hour:

| Clients | Requests | Time |
|---|---|---|
| 100 | ~250 | minutes |
| 400 | ~900 | a little over an hour (one wait) |
| 2000 | ~4500 | five to six hours |

It will get there - the budget makes it wait rather than fail - but for
a large tenant, run it once a day at most, not alongside a backfill, and
consider asking Commvault for a higher limit, which their documentation
says support can grant.

## Scheduling under the rate limit

A delta collection every 15 minutes costs a few requests each, so
perhaps 20 to 40 an hour: comfortable. What uses the budget is
backfill (one request or more per slice) and inventory. Keep those two
from overlapping, and remember each run counts only its own calls.

## What is not known yet

To be filled in from the first probe and runs:

- For an MSP: whether `lhAccountId` alone is enough on every core call,
  whether `GET /api/v1/partner/accounts` still lists accounts and in
  what shape, and whether an MSP access token can be used with a
  customer's `lhAccountId` at all or needs the partner login
  (`POST /api/v1/partner/authentication`), which blt does not use.

- Whether a tenant administrator's access token works, or only
  username and password.
- Which of blt's calls a tenant user is allowed to make. `GET /CommServ`
  and the per-client listings are the ones most likely to be restricted.
- Whether jobs, clients and SQL databases come back in the same shape.
  A tenant's "clients" may be mostly SaaS workloads (Microsoft 365,
  cloud databases, VMs) rather than servers with agents, in which case
  the SQL Server inventory will find little and the inventory needs new
  kinds of object.
- What a `429` looks like in practice, and whether token renewal counts
  as a write.
- Whether `CV_PAGE_SIZE` above 500 is honoured.
- Token lifetimes on a real tenant (the eval CommServe gave 2 hours and
  a 60-day renewal window, not the documented 90).

## Sources

- [Commvault Cloud REST API](https://documentation.commvault.com/saas/rest_api.html) - base URL, credentials, rate limits
- [Commvault REST API reference](https://api.commvault.com/docs/latest/commvault-rest-api-public/) - the SaaS `Authorization` header
- [Commvault API FAQ](https://api.commvault.com/FAQ/) - the MSP headers `lhAccountId`, `tenantGuid`, `ring-id`
- [Access tokens for REST APIs](https://documentation.commvault.com/11.44/software/access_tokens_for_rest_apis_01.html) - token lifetimes
- [Community: using an access token generated in the web portal](https://community.commvault.com/developer-tools-integration-and-automation-50/how-to-use-access-token-generated-in-the-web-portal-in-api-calls-10159) - `/Login` with MFA
