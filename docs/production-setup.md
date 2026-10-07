# Setting blt up against a production CommCell

Step by step, from an empty host to scheduled collection. Written for a
first trial on one or two production CommCells.

**What blt does to a CommServe:** it reads. Job listings, client and
instance listings, job details - all queries. The one thing it changes is
its own access token, when it renews it. Nothing under `lab/` is part of
this: those scripts create clients, subclients and backups and are for
the eval CommServe only. **Do not run anything in `lab/` against
production.**

**What has and has not been proven.** Everything here has been run
against a small eval CommServe (11 SP46, 15 clients, about 120 jobs).
None of it has yet been run against a large or long-lived one. The
things most likely to behave differently at production scale are listed
under "What to watch" at the end, with what to do about each.

## 1. The collector host

One Linux host that can reach each CommServe's web service over HTTPS
and that will also run the database. Needs:

- `podman` (rootless is fine), `git`, and [`uv`](https://docs.astral.sh/uv/).
- Outbound access to GitHub and PyPI the first time, so `uv` can fetch
  dependencies (one of them, `sdk-primer-core`, is a git dependency).
- A few GB of disk for the database volume. A job row is a few KB,
  mostly Commvault's own JSON.

```
git clone <this repo> blt && cd blt
uv sync --extra collector          # fetch dependencies now, not at 2am
```

## 2. The database and API

```
cp config/blt.env.example config/blt.env
chmod 600 config/blt.env
```

Edit `config/blt.env`:

| Setting | Set it to |
|---|---|
| `BLT_API_KEY` | a long random string (`python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`) |
| `POSTGRES_PASSWORD` | another one, letters and digits only (it goes into a URL) |
| `BLT_API_PORT`, `BLT_PG_PORT` | leave, unless 8088 / 5433 are taken |

```
./deploy/up.sh
curl http://127.0.0.1:8088/healthz          # {"status":"ok"}
```

That builds the API image and starts a pod with Postgres 17 and the API.
Both listen on `127.0.0.1` only. Data lives in the podman volume
`blt-pgdata` and survives `up.sh` being re-run (which is also how you
upgrade: `git pull && ./deploy/up.sh` applies any new migrations).

The pod does not start by itself after a reboot; re-run `./deploy/up.sh`.
To have that happen unattended, let the collecting user's services run
without a login session and start the pod from cron (not yet tried
here - check it with a reboot before relying on it):

```
loginctl enable-linger "$USER"
# in crontab -e:
@reboot /path/to/blt/deploy/up.sh >> /var/log/blt/pod.log 2>&1
```

## 3. A Commvault user and token, per CommCell

For each CommServe, in Command Center:

1. **A dedicated user for blt**, not a person's account. It needs to be
   able to *see* jobs, clients, agents, instances and subclients across
   the CommCell. Start with a view-only role on the CommCell.
   *Untested:* which minimum role still returns every job. If blt
   collects fewer jobs or clients than Command Center shows you, this is
   the first place to look.
2. **An access token for that user** (user > Access tokens). Copy both
   the access token and the refresh token; they are shown once.
3. **One token per collector host.** Renewing a token invalidates the
   previous pair, so two hosts sharing one token will lock each other
   out.

On the eval CommServe a token lasts 2 hours and can be renewed for 60
days from when it was created. blt renews it automatically; after the 60
days someone has to create a new one (step 7).

## 4. One config file per CommCell

The file's name is the name blt stores that CommCell's data under, so
pick something stable and short: `config/prod-east.env` is CommCell
`prod-east`.

```
cp config/commcell.env.example config/prod-east.env
chmod 600 config/prod-east.env
```

| Setting | Set it to |
|---|---|
| `CV_BASE_URL` | the REST base URL, no trailing slash - usually `https://<host>/commandcenter/api` |
| `CV_ACCESS_TOKEN`, `CV_REFRESH_TOKEN` | from step 3 |
| `CV_VERIFY_TLS` | leave `true` |
| `CV_CA_BUNDLE` | path to a PEM file, if the CommServe's certificate is signed by a private CA |

Leave the `CV_TOKEN_*` lines alone - blt maintains them - and make sure
the user that will run collections can **write** to this file: renewal
rewrites the token lines in place.

Do not set `CV_VERIFY_TLS=false` for production. If the certificate will
not verify, fix it with `CV_CA_BUNDLE`.

## 5. First contact, in this order

Each step is safe to stop after.

```
./collect.sh prod-east --check
```

Proves the URL, certificate and token. Collects nothing. Fix anything it
reports before going on.

```
./collect.sh prod-east
```

The first run is deliberately small: everything active right now plus
jobs that finished in the last 24 hours. On a busy CommCell that is
still thousands of jobs, fetched 500 at a time; how long a page takes on
a large CommServe is one of the things this trial is to find out. It
ends with a line like `Run 1 succeeded: N collected, ...`.

```
./collect.sh prod-east
```

Run it again. This one is a delta (since the first run, plus an hour of
overlap) and should be quick. If both succeeded, job collection works.

Look at what arrived:

```
podman exec -it blt-postgres psql -U blt -c \
  "select state, count(*) from job group by 1 order by 2 desc"
```

A state of `unknown` means Commvault reported a status blt's table does
not have. It is harmless - the raw status is stored, and the job is
treated as still active - but tell me the status text
(`select distinct status from job where state='unknown'`) so it can be
added to `src/blt/schemas.py`.

## 6. Schedule it

The collector takes a per-CommCell lock for each kind of run, so a run
that overlaps the previous one exits quietly (status 75) instead of
piling up. Cron,
for the collecting user (create `/var/log/blt` first and make it
writable by that user, or log somewhere under their home):

```
# jobs: every 15 minutes
*/15 * * * *  /path/to/blt/collect.sh prod-east             >> /var/log/blt/prod-east.log 2>&1
# older history: a bounded batch each hour until it reports complete, then a no-op
7 * * * *     /path/to/blt/collect.sh prod-east --backfill  >> /var/log/blt/prod-east.log 2>&1
```

Add the backfill line only once you are happy with step 5. Each
invocation fetches at most 30 one-day slices and stops; it remembers
where it got to. To go gently on a large CommCell, start with smaller
batches in the CommCell's `.env`:

```
CV_BACKFILL_MAX_CHUNKS=7
CV_HISTORY_LIMIT_DAYS=365        # do not go further back than this
```

Do **not** use `--full` on a production CommCell: it asks for the whole
history in one query.

## 7. Keep an eye on it

```
-- the last few runs for each CommCell
select c.name, r.mode, r.status, r.started_at, r.jobs_collected, r.error
from collection_run r join commcell c on c.id = r.commcell_id
order by r.id desc limit 20;

-- how far back history reaches
select name, backfilled_to, backfill_complete from commcell;
```

- A `failed` run is recorded with its error and does not move the
  watermark; the next run covers the same ground. One failure is not a
  problem. Repeated ones with the same error are.
- **Token expiry.** `CV_TOKEN_RENEWABLE_UNTIL` in each CommCell's `.env`
  is the date a new token must be created by. Runs log a warning from a
  week before. Put it in a calendar; when it passes, collection stops
  with an authentication error until the two token lines are replaced.
- The log line `Skipping job N: could not read its summary` means one
  job had a field in a shape blt did not expect. The run carries on
  without it. Send me the line.

## 8. Inventory, validation and the SQL report (optional, after jobs work)

```
./collect.sh prod-east --inventory     # what exists: clients, instances, SQL databases
./collect.sh prod-east --validate      # is each database backed up recently?
./collect.sh prod-east --report        # per SQL instance: discovered vs backed up
```

`--inventory` talks to Commvault; the other two only read what it
stored. See docs/blt-flow.md for what the verdicts mean and what a check
built on the backup product's own word can and cannot find.

Try `--inventory` by hand first and time it. It makes two calls per
client, plus several more per SQL Server instance, one after another -
seconds
on the eval CommServe, but on a CommCell with thousands of clients it
could run for many minutes. If it is tolerable, schedule it:

```
# inventory once a day, after the nightly backups
30 7 * * *    /path/to/blt/collect.sh prod-east --inventory >> /var/log/blt/prod-east.log 2>&1
```

Run a job collection shortly before it, so the report can show each
backup job's status.

## Connecting DataGrip (or psql) to the database

| Field | Value |
|---|---|
| Host | `localhost` |
| Port | `5433` (`BLT_PG_PORT`) |
| Database | `blt` |
| User | `blt` |
| Password | `POSTGRES_PASSWORD` from `config/blt.env` |

JDBC URL: `jdbc:postgresql://localhost:5433/blt`. No SSL. Tables are in
the `public` schema. The `blt` user owns them, so mark the data source
read-only in DataGrip unless you mean to edit.

**With podman inside WSL and DataGrip on Windows** the same values
should work: WSL2 forwards Windows' `localhost` to ports listening
inside WSL. Check it before blaming the password:

```
# in WSL - is the port published?
podman port blt-postgres            # expect 5432/tcp -> 127.0.0.1:5433
# in PowerShell - can Windows reach it?
Test-NetConnection localhost -Port 5433     # expect TcpTestSucceeded : True
```

If the second fails, in this order:

1. `wsl --shutdown` from PowerShell, reopen WSL, `./deploy/up.sh`, try
   again. Localhost forwarding sometimes stops until WSL restarts.
2. Check `%UserProfile%\.wslconfig` does not set
   `localhostForwarding=false`.
3. Publish on all of WSL's addresses instead of only its loopback: set
   `BLT_BIND_ADDRESS=0.0.0.0` in `config/blt.env`, then
   `./deploy/down.sh && ./deploy/up.sh` (the data volume is kept), and
   connect DataGrip to the address `hostname -I` prints inside WSL. That
   address changes when WSL restarts.

None of the WSL path has been run by me - this was written on a Linux
host, where the first table works as is.

## Running the collector on Windows

The collector is plain Python and runs on Windows; the database and API
pod do not (they need podman on Linux or WSL). Two ways to split it:

- **Everything under WSL** - the simplest; nothing here changes.
- **Collector on Windows, pod elsewhere** - set `BLT_API_URL` in
  `config\blt.env` to wherever the API is. By default the pod publishes
  on `127.0.0.1` only; with the pod in WSL on the same machine,
  `http://localhost:8088` from Windows should still reach it (see the
  DataGrip section above for how to check). For a pod on a different
  machine, set `BLT_BIND_ADDRESS=0.0.0.0` there - and then the API key
  is the only thing protecting it, over plain HTTP, so keep it on a
  trusted network.

`collect.sh` works from Git Bash, or skip it and call what it calls:

```
uv run --extra collector blt-collect --commcell prod-east --config-dir config
uv run --extra collector blt-collect --commcell prod-east --config-dir config --backfill
```

Overlap protection is inside `blt-collect`, so it applies either way.
Use Task Scheduler in place of cron.

Not yet tried on Windows by me, so treat the first run as a test. Two
things differ there: the `chmod 600` on the `.env` files does nothing,
so protect the `config` folder with its own permissions (it holds the
tokens); and the token file is rewritten on renewal, so nothing else
should have it open.

## More than one CommCell

One `.env` file and one set of cron lines each. They share the database
and the API and do not block one another. Stagger the cron minutes so
they are not all querying at once.

## What to watch, and what to do

| If | It probably means | Do |
|---|---|---|
| `--check` fails on TLS | private CA | set `CV_CA_BUNDLE` |
| `--check` says "Access denied" | wrong token, or it has expired with no refresh token in the file | new token (step 3) |
| First run times out or the CommServe struggles | too much in one window | `CV_INITIAL_LOOKBACK_HOURS=4`, `CV_PAGE_SIZE=200`, `CV_TIMEOUT_SECONDS=300` |
| Fewer jobs or clients than Command Center shows | the blt user cannot see them all | widen its role |
| `returned ... instead of JSON - the CommServe may be in maintenance` | the web tier is restarting | nothing; the next run recovers |
| States of `unknown` | a status blt's table lacks | send me the status text |
| Backfill slices are slow | a day is too big a slice here | `CV_BACKFILL_CHUNK_HOURS=6` |
| Inventory takes too long | per-client calls, done in sequence | do not schedule it yet; this needs batching (TODO item 16) |
| Job times look hours out | the CommServe's clock, not blt | check the CommServe; blt stores what it is given |

## Backing it out

```
crontab -e                          # remove the blt lines
./deploy/down.sh                    # stop the pod
podman volume rm blt-pgdata         # only if you also want the data gone
```

Then delete the blt user's access token in Command Center. Nothing was
installed on, or changed in, the CommServe itself.
