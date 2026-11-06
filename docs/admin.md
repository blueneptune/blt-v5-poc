# Administration: managing blt from its own UI

**Status: built 2026-11-05 (branch F/Login_admin).** blt's UI now does
what Prefect's UI was used for, and Prefect serves no UI of its own. The
other UIs blt sits on (Harbor, Rancher, Longhorn, Grafana later) follow
the same way, one at a time.

## Signing in

One local account, until Zitadel ([vision.md](vision.md) 4.1):

| Setting | |
|---|---|
| `BLT_UI_USER` | its name, default `admin` |
| `BLT_UI_PASSWORD_HASH` | the password's scrypt hash - only the hash is kept anywhere |
| `BLT_UI_SESSION_SECRET` | signs the session cookie; 32+ random characters |

Make both with `cd ui && npm run password -- --secret` (it asks for the
password twice and shows neither) and put them in `config/blt.env`, or in
the cluster's secret. A new password, or a new secret, signs everyone out.

- Every page but `/healthz` and `/login` needs signing in. The session is
  an HttpOnly, SameSite=Lax cookie, Secure over https, for 12 hours.
- Five wrong passwords from one address lock it out for 15 minutes.
- Without the two settings the UI refuses to serve rather than opening.
  `BLT_UI_SIGN_IN=off` opens it on purpose - the start scripts allow that
  only with the UI on `127.0.0.1`.
- Over plain http the password crosses the network as typed: put https in
  front (the cluster's ingress does) before opening it to other machines.

## Sources (Administration -> Sources)

Every CommCell, Rubrik account and NetBackup primary - Prefect's blocks -
with whether each secret is set, never its value. Any of them can be
switched on and off for the scheduled flows.

**A NetBackup primary** is added here end to end:

1. **Name and address.** A host name is enough
   (`https://<host>:1556/netbackup` is made from it).
2. **Fetch its CA** (optional): a worker asks the primary for its CA
   certificate - NetBackup gives it without signing in - and the page
   shows each one with its SHA-256 fingerprint and whether it verifies
   the primary at that address. Compare the fingerprint with the
   primary's own (`nbcertcmd -displayCACertDetail` on it) before **use
   it**. Or paste the PEM yourself.
3. **API key** - for a read-only service account (below), never shown
   again once saved.
4. **Save and check.** The block is saved with collection **off**, and a
   check runs on a worker: sign-in and server version, storage, SLP images,
   jobs - one OK/FAIL line each, with why (a refused key, a permission the
   key's role lacks, an endpoint this NetBackup version does not have).
5. **Turn on** collection once storage passes.

Its page also takes a new key, address or CA certificate, and removes it
(typing its name to confirm). CommCells and Rubrik accounts still get
their credentials from `blt-prefect-import` and `blt-rubrik`.

### The NetBackup account

NetBackup ties an API key to a user, one key per user. Make a service
account for blt rather than using a person's:

1. A user for blt on the primary's authentication domain - a local user
   on the primary, or an AD account - named for what it does (e.g.
   `blt_r`).
2. A role for it in the web UI (Security -> RBAC) that may only read:
   storage (disk pools, storage units, storage servers), the catalog
   (images, SLP images) and jobs.
3. An API key for that user (Security -> API keys -> Add, choosing the
   user), with an expiry you will remember to renew.

The check says which of blt's reads the role is missing.

## Schedules (Administration -> Schedules)

Prefect's deployments: when each runs (schedules are UTC), its last run
and next one. **Pause / Resume** per deployment - a pause stays when the
worker restarts. **Run now** opens a form from the flow's own
parameters; a by-hand flow such as `blt-backfill/by-hand` is run from
here with its CommCell's name. The schedules themselves are set in
`prefect.yaml`.

## Runs (Administration -> Runs)

Flow runs newest first, by state (running, failed, completed,
scheduled) or one deployment's. A run's page shows its log live while it
runs, and can cancel it.

## How it works

```
browser --(signed-in session)--> blt UI (server side) --(X-API-Key)--> blt API /v1/admin
                                                                          |
                                                    Prefect REST API <----+ (BLT_PREFECT_API_URL,
                                                          |                  BLT_PREFECT_AUTH)
                                                    worker: flows that reach the backup products
```

- The API talks to Prefect's REST API over plain HTTP (`blt.api.prefect`);
  its image carries no Prefect package.
- Nothing reaches a backup product from the API: checks and CA fetches
  are flow runs (`blt-netbackup-check/by-hand`,
  `blt-netbackup-fetch-ca/by-hand`), so they work from whichever zone the
  worker is in ([vision.md](vision.md), network zones).
- A secret goes in once: the API passes it to Prefect and answers only
  with which secrets are set.
- Prefect's server runs with `PREFECT_SERVER_UI_ENABLED=false` (set in its
  image). Its API stays, behind `PREFECT_AUTH`, for the worker, blt's API
  and the `blt-prefect-*` tools.

## Not yet

- Adding CommCells (with their token handover) and Rubrik accounts here.
- Work pools and workers: whether a worker is connected.
- Users and roles: one admin account until Zitadel.
- The other UIs (Harbor, Rancher, Longhorn).
