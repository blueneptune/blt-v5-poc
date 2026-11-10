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

Make both with `cd ui && npm run --silent password -- --secret >>
../config/blt.env` (it asks for the password twice and shows neither;
`--silent` keeps npm's own banner out of the file), or put them in the
cluster's secret. A new password, or a new secret, signs everyone out.

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
switched on and off for the scheduled flows, and given a **collector**:
the worker that reaches it, blt's own or a remote one in the source's own
network ([remote-collector.md](remote-collector.md)). Above the table,
**Collectors** lists each worker, with whether it is online and how
many sources it reaches.

**Add a source** (the drop-down above the table) opens the form for the
product chosen: a NetBackup primary, a Commvault CommCell, or a Rubrik
account. Each saves the source's block with collection **off**, starts a
check on the worker that reaches it, and opens the source's page to watch
the check's log: one OK or FAIL line per read blt makes, with why. Turn
collection on there once the reads pass.

**A NetBackup primary** is added here end to end:

1. **Collected by** (only offered once a remote collector exists): the
   collector that can reach it. Its CA is fetched, and it is checked,
   from there.
2. **Name and address.** A host name is enough
   (`https://<host>:1556/netbackup` is made from it). Some NetBackup
   10.5 primaries serve the API only through their web server, on 443:
   when 1556 does not answer, **Fetch its CA** tries 443, says so, and
   puts that address in the form. Type `<host>:443` to go there
   directly; a port you give is kept. A check of a saved primary that
   cannot sign in on 1556 says when 443 answers instead.
3. **Fetch its CA** (optional): a worker asks the primary for its CA
   certificate - NetBackup gives it without signing in - and the page
   shows each one with its SHA-256 fingerprint and whether it verifies
   the primary at that address. Compare the fingerprint with the
   primary's own (`nbcertcmd -displayCACertDetail` on it) before **use
   it**. Or paste the PEM yourself.
   A primary whose NetBackup CA has a **1024-bit key** - one made by an
   older install (2017, say) and kept through every upgrade - is refused
   by today's TLS ("CA certificate key too weak"). The fetch says when a
   certificate verifies only with **legacy key sizes accepted**, and using
   it ticks that box. It is that primary's own setting (its page can turn
   it off again) and relaxes only the key size: the chain, expiry and host
   name are still checked. The Sources list marks such a primary "legacy
   TLS keys"; the lasting fix is a NetBackup CA migration to a 2048-bit CA.
4. **API key** - for a read-only service account (below), never shown
   again once saved.
5. **Save and check.** The block is saved with collection **off**, and a
   check runs on a worker: sign-in and server version, storage, SLP images,
   jobs - one OK/FAIL line each, with why (a refused key, a permission the
   key's role lacks, an endpoint this NetBackup version does not have).
6. **Turn on** collection once storage passes.

Its page also takes a new key, address or CA certificate, and removes it
(typing its name to confirm).

**A Commvault CommCell** - a CommServe you run, or a Commvault Cloud
tenant:

1. **Where it runs.** A CommServe needs its address: a host name is
   enough (`https://<host>/commandcenter/api` is made from it; give a
   whole URL for another path, such as `.../webconsole/api`). Commvault
   Cloud needs none (`https://api.metallic.io`); an MSP's customer also
   takes the customer's account id or tenant GUID.
2. **Signing in**, one of:
   - a **user name and password** for a Commvault user whose role may only
     read - blt signs in for each run;
   - an **access token** from Command Center, with its **refresh token**
     so blt renews it into the block. Once blt has the pair, use it
     nowhere else: a renewal anywhere else ends blt's (TODO 15), and a new
     token has to be made. Without a refresh token it simply expires.
3. **Save and check**: sign-in (the CommServe's name and version), jobs of
   the last 24 hours, storage.

Its page changes the address, or replaces the way it signs in whole (a
token clears the user and password, a password the tokens), and switches
TLS verification - off only for a lab CommServe with a self-signed
certificate.

**A Rubrik account** is added from its **service account file**: make a
service account in Rubrik Security Cloud (Settings -> Users -> Service
Accounts) with a read-only role and download its JSON file, then choose
that file. The page reads `client_id`, `client_secret` and
`access_token_uri` from it and sends them to the API; the secret is kept
in the block and never shown again, and blt does not need the file after.
A file that is not a service account (no `/api/client_token` address) is
refused, saying so. The check signs in and reads jobs, inventory and SLA
domains. Its page takes a new file after the secret is rotated in RSC.

The command-line imports still work: `blt-prefect-import` for CommCells'
`.env` files (and an MSP's credentials block, which the form does not
make), and `blt-rubrik add`.

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

### Stuck runs

Above the schedules, **Run health** says whether runs are getting
through. Each per-source run takes one of the collection slots
(`BLT_MAX_PARALLEL`, a Prefect global concurrency limit) while it
collects. It warns when:

- **slots are held by runs that are gone** - Prefect counts more slots
  taken than there are runs collecting, so new runs queue for them for
  ever (they show as RUNNING, doing nothing). Until 2026.11.09.2 a
  restart of the Prefect server did this: it kept who held each slot
  only in memory.
- **runs are stuck** - RUNNING for over 2 hours, or PENDING or
  CANCELLING for over 10 minutes.

**Clear stuck runs** (confirmed first) cancels those runs at once - no
worker is running them - and brings the slot count down to the runs
still collecting, never below: a run that holds a slot keeps it. The
next scheduled runs catch up. Without the UI:

```bash
deploy/maintenance.sh status             # exit 1 when runs are stuck
deploy/maintenance.sh unstick --dry-run  # what it would do
deploy/maintenance.sh unstick
# on the cluster:
kubectl -n blt exec deploy/blt-api -- python -m blt.api.maintenance status
```

Two things keep it from happening in the first place:

- The Prefect server keeps its slot leases on disk, in the
  `blt-prefect-home` volume (a PVC on the cluster), so they outlive a
  restart; a slot whose run is gone comes back when its lease runs out,
  within 5 minutes.
- Each flow run sends a heartbeat every 30 s; the automation
  `blt-crash-silent-runs`, which blt's worker sets up as it starts, marks
  a run Crashed when it has been silent for 5 minutes (its process is
  gone - a worker restart, a machine down). Remote collectors send
  heartbeats too, so 5 minutes also rides out a short drop of their ssh
  tunnel.

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
  worker is in ([vision.md](vision.md), network zones) - and each goes to
  the source's own collector, as does a run-now for one source.
- `GET /v1/admin/collectors` reads the work queues of the `blt` pool:
  a worker that asked for work in the last minute or so is online.
- A secret goes in once: the API passes it to Prefect and answers only
  with which secrets are set.
- Prefect's server runs with `PREFECT_SERVER_UI_ENABLED=false` (set in its
  image). Its API stays, behind `PREFECT_AUTH`, for the worker, blt's API
  and the `blt-prefect-*` tools.

## Not yet

- An MSP's shared credentials block for Commvault Cloud (one login lent to
  many customer blocks): still `blt-prefect-import`.
- Users and roles: one admin account until Zitadel.
- The other UIs (Harbor, Rancher, Longhorn).
