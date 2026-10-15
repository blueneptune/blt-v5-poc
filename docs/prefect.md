# blt on Prefect

Prefect schedules blt's collection, retries what fails, and keeps a
record of every run with its log. CommCell settings and credentials move
out of `.env` files into Prefect **blocks**.

**Status:** working on the Linux dev machine against the eval CommServe
(collection, inventory and backfill as Prefect flow runs, token renewal
into a block), with both start scripts and both network modes. **Not
yet run on Windows with podman in WSL.**

## What runs

One pod, `blt`, as before, with two more containers:

```
blt pod
├── blt-postgres         Postgres 17: database "blt" (jobs) and
│                        database "prefect" (Prefect's own), one volume
├── blt-api              blt's API, as before
├── blt-prefect-server   Prefect API and UI, port 4200
└── blt-prefect-worker   blt's flows + a process worker on pool "blt"
```

- **Prefect's database** is a separate database, `prefect`, on the same
  Postgres, owned by its own `prefect` user. The server creates both the
  first time it starts. It lives in the `blt-pgdata` volume with the job
  data.
- **The worker** is built from this repository with the `prefect`
  extra. When it starts it registers the block type and the deployments
  in `prefect.yaml`, so a new image brings its schedules with it. A code
  change reaches Prefect by rebuilding: run the start script again.

## The flows

| Deployment | Flow | Schedule (UTC) | Does |
|---|---|---|---|
| `blt-collect-jobs/hourly` | `collect_jobs` | every hour at :07 | `collect.sh <commcell>` for every CommCell block |
| `blt-collect-inventory/twice-daily` | `collect_inventory` | 05:37 and 17:37 | `collect.sh <commcell> --inventory` for every CommCell block |
| `blt-backfill/by-hand` | `backfill` | none | `--backfill` for one CommCell, batch after batch until complete |

- "Every CommCell block" means every block of type *blt Commvault
  CommCell* with **collect** on. Each CommCell runs as its own child
  flow run (`collect-cv-toaster`, ...): one failing does not hold up the
  others, it is retried once after five minutes, and the parent run is
  marked failed if any CommCell still failed.
- A run for a CommCell that is still going when the next one starts is
  skipped, not doubled up (the same per-CommCell lock as collect.sh).
- blt's own log lines appear in each flow run's log in the UI.
- Schedules are changed in `prefect.yaml` (then restart the worker), or
  paused per deployment in the UI. A paused schedule stays paused when
  the worker restarts.
- **Backfill** from the UI: *Deployments → blt-backfill/by-hand → Run →
  Custom run*, and give `commcell`. Once a CommCell's history is in, it
  is not needed again.

## Turning it on

In `config/blt.env`:

```
BLT_PREFECT=true
PREFECT_DB_PASSWORD=<a new password>
PREFECT_AUTH=admin:<a new password>
# BLT_PREFECT_PORT=4200
# BLT_PREFECT_BIND_ADDRESS=127.0.0.1
```

`PREFECT_AUTH` is the login for the UI and API. Prefect's open-source
server has no user accounts; this one shared login is all that guards
it, and the blocks behind it hold Commvault credentials.

Then recreate the pod (data is kept):

```
./deploy/down.sh
./deploy/up.sh          # or: ./deploy/kube/up.sh
```

The first start builds two more images and takes a few minutes. It ends
with `Prefect UI on http://<address>:4200`.

### Who can reach the UI

`BLT_PREFECT_BIND_ADDRESS` sets this for the UI alone; the API and the
database keep `BLT_BIND_ADDRESS`.

- **The Linux dev machine (eth00dev1)**, opened from your desktop:
  `BLT_PREFECT_BIND_ADDRESS=0.0.0.0`, then browse to
  `http://192.168.50.187:4200`. The login travels unencrypted over the
  LAN (plain HTTP with basic auth) - acceptable for a dev box, not for
  anything wider.
- **Your desktop (podman in WSL)**, used only there: leave it unset, so
  it follows `BLT_BIND_ADDRESS=127.0.0.1`, and browse to
  `http://127.0.0.1:4200`.

### Podman in WSL

Same as the existing setup there: keep `BLT_POD_NETWORK=host` (podman's
port forwarding does not work under WSL, as found before). On the host
network each service listens on its own port, so the UI is on
`BLT_PREFECT_PORT` directly. Both start scripts handle it; the manifest
drops its port mappings in that mode because podman refuses them.

Not yet tried there: the worker image build (it fetches the two SDKs
from GitHub during the build, so the WSL machine needs to reach
github.com), and `podman kube play` reading the manifest from a pipe
through the Windows podman client. If either fails, send me the output.

## Moving a CommCell's credentials into a block

```
# where Prefect is, and its login, for the commands below
export PREFECT_API_URL=http://127.0.0.1:4200/api
export PREFECT_API_AUTH_STRING='admin:<the password>'

uv run --extra prefect blt-prefect-import cv-toaster    # one
uv run --extra prefect blt-prefect-import --all         # every config/*.env but blt.env
```

It prints what it imported - deployment, whether it is collected, which
secrets it carried - and never a value. After that the block is visible
in the UI under *Blocks*, secrets masked, and can be edited there.

- **An MSP's login file** (one that other files name in
  `CV_CREDENTIALS`) becomes a block with *collect* off: it lends its
  credentials to the customer blocks and is not collected from itself.
  The customer blocks name it in *credentials*, exactly as the files do.
- Settings without a field of their own (`CV_PAGE_SIZE`,
  `CV_BACKFILL_CHUNK_HOURS`, ...) are kept in the block's *options*.
- Without `--overwrite`, an existing block is not replaced.

### One home for each token

A Commvault token pair can be used from one place only: renewing it
makes the old pair worthless, and the next renewal attempt from the
other place fails. So once a CommCell is imported:

- collect it **from Prefect only**; the copy in its `.env` file goes
  stale the next time Prefect renews (within two hours);
- to go back to `collect.sh` for it, first copy the current tokens out
  of the block, then delete the block (or turn its *collect* off):

  ```
  uv run --extra prefect blt-prefect-import --to-env cv-toaster
  ```

`collect.sh` keeps working as before for every CommCell that has not
been imported.

## Kubernetes manifest

`deploy/kube/blt.yaml` is the same pod as a Kubernetes manifest: a
Secret with the passwords, a PersistentVolumeClaim for `blt-pgdata` (the
same volume `deploy/up.sh` uses), and the Pod. `deploy/kube/up.sh` fills
it in from `config/blt.env` and passes it to `podman kube play` through
a pipe, so the filled-in copy with passwords is never written to disk.

```
./deploy/kube/up.sh             # build the images and play the manifest
./deploy/kube/up.sh --render    # print the filled-in manifest, passwords masked
```

- `deploy/up.sh` stays until this has proven itself. Both read the same
  settings from one place (`deploy/env.sh`), name the containers the
  same, and use the same volume, so you can switch between them with
  `./deploy/down.sh` in between. Re-running either is safe; the data
  is kept.
- `podman kube play` keeps the Secret in podman's secret store, which by
  default is a file under your home directory, readable by your user.
- For a real cluster, the next step is one Deployment per service with
  a Service in front of each, and the images pushed to a registry. One
  pod keeps the local setup identical to `deploy/up.sh` for now.

## Looking at it

```
podman logs -f blt-prefect-worker     # registrations, then every flow run
podman logs blt-prefect-server        # database setup, then the server
```

In the UI: *Runs* for history and logs, *Deployments* for schedules and
running something now, *Blocks* for CommCells, *Work pools → blt* to see
the worker is connected.

## Not done yet

- **BLT_API_KEY** and the blt API's address still come from
  `config/blt.env` (passed to the worker), not a block. They are blt's
  own, not a Commvault credential.
- **One worker.** Token renewals for a shared MSP login take turns
  through a lock inside the worker; a second worker on another machine
  would need a Prefect concurrency limit instead.
- **The server creates Prefect's database** using blt's own Postgres
  user, so the server container is given that password.
- **Notifications** on failure (email, Teams) - Prefect automations can
  do this; nothing is set up.
- **Schedules are UTC.** Say if they should follow a local time zone.
