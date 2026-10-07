# Lab noise: giving blt something to collect

A small rig, separate from blt, that makes the eval CommServe
(`cv-toaster`) produce real jobs with different outcomes, so blt's
collection, delta and reconcile logic can be tested against the real
thing. Built to be re-run from scratch each time the eval VM is
redeployed.

**Status (2026-10-06):** both stages work. Ten container clients
(`blt-lab-01` to `-10`) are registered and running alongside the four
subclients on the CommServe itself - see "Post-setup review" for what
happened on each run. Teardown has not been run.

## Stage 1 - back up the CommServe's own files

The CommServe is itself a file-system client. Stage 1 gives it a few
small subclients and runs backups against them. Nothing is installed
anywhere and no other machine is involved.

```
lab/00-preflight.sh     # look, change nothing
lab/10-setup.sh         # create the blt-lab-* subclients
lab/20-noise.sh         # one round of jobs
./collect.sh cv-toaster # let blt collect them
```

All four lab scripts take an optional CommCell name (default
`cv-toaster`) and read the same `config/<commcell>.env` blt does,
including token renewal.

### What setup creates

On client `CV-TOASTER`, File System agent, `defaultBackupSet`, under
`Standard Plan` (which already exists, with `Standard Storage Pool`):

| Subclient | Content | There to produce |
|---|---|---|
| `blt-lab-etc` | `C:\Windows\System32\drivers\etc` | a job that completes in seconds |
| `blt-lab-drivers` | `C:\Windows\System32\drivers` | a second, mid-sized ordinary job |
| `blt-lab-fonts` | `C:\Windows\Fonts` | a job long enough to suspend and kill |
| `blt-lab-missing` | `C:\blt-lab\does-not-exist` | a job that does not complete cleanly |

Re-running setup skips whatever already exists. It ends by reading each
subclient back and reporting its plan and content, and warns if a plan
did not attach.

### What a round of noise does

1. Starts a backup on `etc`, `drivers` and `missing` - fulls on the
   first round, incrementals after.
2. Starts a full on `fonts`, waits for it to be running, **suspends** it,
   leaves it suspended for 20 seconds, **resumes** it, then **kills** it.
3. Waits for everything to finish and prints each job's final status.

Options: `--loop MINUTES` repeats (add `--rounds N` to stop),
`--no-drama` skips the suspend/kill, `--level Full|Incremental` forces a
level, `--plan NAME` uses a different plan.

To watch blt follow jobs through their states, run `./collect.sh
cv-toaster` in a second terminal while a round is in progress, then
again after.

### Teardown

`lab/90-teardown.sh` kills any lab job still running and deletes the
`blt-lab-*` subclients. The backed-up data ages out under the plan's own
retention. It touches nothing that isn't named `blt-lab-*`.

### What has and hasn't been proven

| | |
|---|---|
| Login, lookups, plan / storage / agent checks | Proven - `00-preflight.sh` |
| Creating subclients with a plan | Proven - `10-setup.sh`; all four read back with the plan attached |
| Starting backups | Proven - four fulls started |
| Suspend / resume / kill | Proven - job 6 went Suspended, resumed, then Killed |
| Incremental rounds, `--loop` | **Not run yet** |
| Deleting subclients | **Not run yet** - `90-teardown.sh` |

## Stage 2 - containers as extra clients

Podman containers on this machine, each a bare Rocky 9 "server" that
installs the Commvault file system agent on first start and registers
itself with the CommServe as `blt-lab-01`, `blt-lab-02`, ... Jobs then
differ by client as well as by outcome.

```
lab/30-clients.sh             # 3 containers; --count N for more
lab/20-noise.sh               # now also backs up every container client
./collect.sh cv-toaster
```

Built 2026-10-05, first run the same night; see "Post-setup review".

### How it works

1. **Media.** The CommServe lists its downloadable client packages
   (`GET /V4/commcell/available-packages`) with public URLs on
   Commvault's download site. The script downloads
   `LinuxFileServer64.tar` (547 MB), checks it against the CommServe's
   SHA-256, and unpacks it to `lab/media/` (gitignored). Already done
   once by hand; the script skips it when the media is there.
2. **Image.** `blt-lab-agent` (`lab/client-image/`): Rocky 9 plus the
   handful of packages the installer needs. No Commvault software in it.
3. **Authcode.** The script asks the CommServe for a CommCell install
   authcode (`POST /Organization/0/Authtoken`), which lets an installer
   register a client without a user name and password. **This turns on
   authcode-based installation for the CommCell** - it was off.
4. **Containers.** Each is started with the media mounted read-only. Its
   entrypoint writes one or two files under `/data`, fills in Commvault's
   own answer file (CommServe name, client name, authcode) and runs
   `silent_install`. The answer file already asks for "client opens the
   connection to the CommServe", so **no ports are published** and
   nothing on the network can connect in to a container.
5. **Subclient.** Once a container shows up as a client, the script
   gives it a subclient `blt-lab-data` -> `/data` under `Standard Plan`.
   The default subclient (the container's whole filesystem) is left with
   no plan, so it is never backed up.

`20-noise.sh` appends a line to `/data/notes.txt` in every running
container before each round, so incrementals have a change to pick up.

`90-teardown.sh` now also deletes the `blt-lab-NN` clients from the
CommServe and removes the containers.

### What has and hasn't been proven

| | |
|---|---|
| Media download and checksum | Proven |
| Agent installs in a rootless container | Proven - ten times |
| Getting an authcode | Proven |
| Registration over the outbound-only connection | Proven, when containers register one at a time |
| Services start without systemd | Proven on fresh containers |
| Subclients and full backups on a container client | Proven on `blt-lab-01` and `-03` |
| Re-running the script over existing clients | Proven |
| Incrementals, a container restart | **Not run** |
| Deleting clients (teardown) | **Not run** |

The agent in the package is 11.46.6; the CommServe is 11.46.30. A client
older than its CommServe is normal, but it is a difference to remember
if registration complains about versions.

## Stage 3 - a SQL Server client, and backup validation

One container, `blt-lab-sql-01`: Rocky 9 running SQL Server 2022
(Developer edition - free, non-production use only; building the image
accepts Microsoft's licence terms) with ten user databases, plus the
Commvault SQL Server agent. It exists to answer one question for real:
*does Commvault have a backup of every database that exists on this
host?*

```
lab/40-sql.sh              # container, agent, registration, login, plan
lab/45-sql-backup.sh       # Full, Differential, Transaction Log
lab/50-sql-validate.sh     # the check itself (read-only)
```

`uv run --extra collector python lab/cvlab.py sql-add-db` adds one more
database with no backup, to prove the check catches it.

### The validation

For every database SQL Server says exists (`sys.databases`, asked of SQL
Server directly), look it up in Commvault's list for that instance
(`GET /sql/databases?instance=<id>`, which gives each database's last
backup time):

| Verdict | Means |
|---|---|
| `ok` | Commvault has a backup newer than `--max-age-hours` (default 24) |
| `STALE` | it has one, but older than that |
| `UNPROTECTED` | it has none |
| `gone` | Commvault has backups of a database that no longer exists |
| `unverified` | `master`, `model`, `msdb`: Commvault backs them up, but this endpoint only lists user databases, so it can't prove it |
| `n/a` | `tempdb`, which is never backed up |

Exit status 0 only if nothing is `UNPROTECTED` or `STALE`.

The point of taking "what exists" from SQL Server rather than from
Commvault: before the first backup, Commvault listed **zero** databases
on this instance while SQL Server had fourteen. A check built on
Commvault's own list would have reported that host clean.

Proven 2026-10-06: 10 of 10 after the first full backup; then, with
`lab_db_11` added and not backed up, "10 of 11 ... 1 PROBLEM(S)", exit 1.

### What it took to make SQL Server back up in a container

All found the hard way on the first run; all now in the image or script.

| Symptom | Cause | Fix |
|---|---|---|
| Commvault installs a **Sybase** agent and starts its own "Install Software" job | Its installer auto-detects applications and takes SQL Server on Linux for Sybase | None needed - harmless, and that job also brought the client up to the CommServe's version. It does restart the agent, which exposed the next row. |
| After that job the client is offline for good; `commvault start` says "All services started" and starts nothing | Nothing in the container reaped exited processes, so the stopped services lingered as zombies and the start command took them for running | Both images' entrypoints now stay as PID 1 and reap |
| No SQL instance is ever discovered | Discovery runs `systemctl status mssql-server`; there is no systemd | A stand-in `systemctl` in the SQL image answers that one query from the process table, refuses everything else, and logs every call. **Lab workaround** - a real server has systemd. |
| Instance discovered but "Failed to verify the credentials" | On Linux Commvault needs a SQL-authenticated sysadmin login set on the instance | The script stores `sa` as a Commvault credential and attaches it |
| Every backup fails at `OpenDevice Failed [0x80770004]`, SQL Server error 995 | SQL Server in a container will not hand backups to a backup tool unless `memory.enablecontainersharedmemory` is on, **and** `/dev/shm` is bigger than the 64 MB default (Microsoft's container configuration page) | Entrypoint sets the option; the container is created with `--shm-size=2g` |

Ruled out along the way: SELinux. The failure was identical with the
container unconfined, so the container is confined again and the script
creates it that way. *A backup has not yet been run since it was
re-confined* - the next `45-sql-backup.sh` is that test.

The `sa` password is generated once and kept in `lab/.state/`
(gitignored).

### Report scenarios (`./collect.sh cv-toaster --report`)

Four cases the per-backup report has to get right. Each is also a unit
test (`tests/test_inventory.py`); these are the same cases against the
real CommServe. After each step that changes something, refresh with
`./collect.sh cv-toaster && ./collect.sh cv-toaster --inventory`.

| # | Case | How it is staged | Expected line |
|---|---|---|---|
| 1 | Full backup of everything | `lab/45-sql-backup.sh --levels Full` | discovered = backed up, "all discovered databases backed up" |
| 2 | Partial backup | `cvlab.py sql-offline --database lab_db_05`, then a Full | one fewer backed up, "missed: lab_db_05" |
| 3 | Databases added after the full | `cvlab.py sql-add-db`, no backup | **unchanged** - Commvault has not seen the new database; the next Full discovers and takes it |
| 4 | SQL agent installed, never SQL-backed-up | `lab/40-sql.sh --sql-client blt-lab-sql-02 --databases 5` and nothing else | a line with no job, 0 discovered, "no SQL backup has ever run here" |

(`cvlab.py ...` is `uv run --extra collector python lab/cvlab.py ...`.)

Results so far:

- **1 - passed 2026-10-06.** `112  Completed  14  14  all discovered
  databases backed up`.
- **3 - "before" half seen.** `lab_db_12` was created and not backed up;
  the inventory still lists 14 databases and the report still reads
  14/14, while `lab/50-sql-validate.sh`, which asks SQL Server, reports
  it UNPROTECTED.
- **2 and 3 - passed 2026-10-06** (job 117, a Full with `lab_db_05`
  offline and `lab_db_12` never yet backed up):
  `117  Completed  15  14  missed: lab_db_05; job reports 1 skipped`,
  exit 2.
  - Commvault **skipped the offline database and still ended the job
    `Completed`** - not "completed with errors". The job's status alone
    gives no hint that a database was left out; only its skipped count
    (1) and the per-database comparison do.
  - It kept `lab_db_05` in its list (so it is still "discovered"), with
    its last full left pointing at the earlier job. That is what lets
    the report name it.
  - `lab_db_12` was discovered and backed up by this job: discovered
    went from 14 to 15 with no finding ever raised for it, as expected.
  - `--validate` still calls `lab_db_05` `ok`, correctly by its own
    rule: its last backup is a few hours old, inside the 24-hour limit.
    The two answer different questions - "is there a recent backup" and
    "did the latest full take everything".
- **4 - not run.** A file system backup of that client is not part of
  it: the SQL package does not register a file system agent with the
  CommServe, and it does not change what the case shows.

### Still to do

- Run report scenarios 2, 3 (second half) and 4.
- Verification of system databases, by another route than
  `/sql/databases`.
- PostgreSQL, the same way.
- Teardown of the SQL clients and their stored credential.

## Facts about this lab, as found

| | |
|---|---|
| CommServe | `CV-TOASTER`, Windows, 11 SP46.30, also the only MediaAgent |
| Reachable from here | 443, 81, 8400, 8403, 3389, 5985. Not 22 or 445. |
| Certificate | Self-signed for `WINONPFGER.gp.cv.commvault.com`; `CV_VERIFY_TLS=false` is required |
| Storage / plan | `Standard Storage Pool` (about 1 TB free), `Standard Plan` |
| Token's user | `Admin` |
| Software cache | `J:\Program Files\Commvault\ContentStore\SW`, Linux x86_64 media since job 1 (not used by the lab in the end) |
| Clock | About two hours ahead of this machine in absolute terms |
| This machine | Rocky 10.2, podman 5.8.2, no firewall, `sudo` needs a password |

## Post-setup review

### First run, 2026-10-05 (evening)

Run by hand from the prompt, in order, with no errors and nothing fixed
up manually.

| Step | What happened |
|---|---|
| `lab/00-preflight.sh` | Passed: File System agent present, `Standard Plan` found, `Standard Storage Pool` online. |
| `lab/10-setup.sh` | Created `blt-lab-etc` (id 7), `-fonts` (8), `-drivers` (9), `-missing` (10). Also performed the first real token renewal on the way in. |
| `lab/20-noise.sh` | Jobs 3-6, all fulls: `etc` Completed, `drivers` Completed, `missing` Failed ("Configured content does not exist"), `fonts` suspended, resumed, then Killed. About two minutes in all. |
| `./collect.sh cv-toaster` | Delta run: 6 collected, 0 reconciled, 0 missing. States stored: 4 completed, 1 failed, 1 killed, none `unknown`. |

Also on the CommServe, not from the lab scripts: job 1 (Download
Software for Linux x86_64, about 23.6 GB, Completed) and job 2 (Software
Sync, which Commvault started by itself afterwards).

Differences from the plan: none in what the scripts did.

Things noticed:

- Job 1 was first collected as `Running` and on the next run was updated
  in place to `Completed` - the delta path working on a real job.
- Job 1 reports a **negative elapsed time** (-5445 s). The CommServe's
  clock was corrected while the job was running, so its end is "before"
  its start. A Commvault-side artefact of the clock fix; blt stores what
  it is given.
- Nothing has exercised the reconcile pass against the real CommServe
  yet: every job was inside the delta window. Catching a job while it is
  suspended needs a `collect.sh` run *during* a noise round.

### Container clients, first run, 2026-10-05 (late evening)

`lab/30-clients.sh` with the default three containers.

| | |
|---|---|
| Authcode | Obtained; authcode-based installation is now on for the CommCell. |
| `blt-lab-01`, `blt-lab-03` | Installed (11.46.6), registered over the outbound-only connection, and each got `blt-lab-data` -> `/data` under `Standard Plan` (subclient ids 15 and 16). |
| `blt-lab-02` | Agent installed but **registration failed**: "Failed to register with CommServe. Error: Failed in initializing thread pool" (installer exit 59). |

Cause, as far as the evidence goes: all three were started in the same
second and registered at once. Not confirmed by a second failure.

Changed as a result: containers are now started one at a time, each
waited on until it is a client before the next starts; a failed
registration is detected from the installer's exit code and retried
once; and a re-run removes a leftover container that never registered
instead of just starting it again. The script also no longer sits for 15
minutes waiting on a container that has already failed.

Still to do: re-run `lab/30-clients.sh` to bring `blt-lab-02` in, then a
noise round across the container clients.

### Container clients, first backups, 2026-10-06 (just after midnight)

`lab/20-noise.sh` across the CommServe's subclients and the two
registered container clients (jobs 7-12).

**Problem found:** the container backups (jobs 10 and 11) went to
`Pending` with "Failed to start phase [Scan] ... No direct tunnel to
blt-lab-01". The agents had installed and registered, but no Commvault
services were running in the containers: the control script hands
`commvault start` to `systemctl`, and the containers have no systemd.

**Fix:** Commvault's script has its own switch for this - a marker file,
`/tmp/cvpkgadd_unlock_nosystemd_nosysv` - which makes it start the
services directly. Applied by hand in `blt-lab-01` and `blt-lab-03`
(marker file, then `commvault start`); the tunnel to the CommServe came
up immediately and both pending jobs completed on Commvault's next
retry, about two minutes later, with nothing done on the CommServe side.
The entrypoint now creates the marker and starts the services itself,
for new containers and on restart.

Result of the round: `etc` and `drivers` Completed, `missing` Failed,
`fonts` suspended / resumed / Killed, `blt-lab-01` and `blt-lab-03`
Completed (2 files, 264 KB each).

**blt followed two real jobs through a state change:** a collection
taken while 10 and 11 were stuck stored them as `pending` / active; the
next collection updated the same two rows to `completed`.

### Ten container clients, 2026-10-06

`lab/30-clients.sh --count 10`, about six minutes.

- `blt-lab-01` and `-03` were recognised as existing clients and left
  alone; the leftover `blt-lab-02` container was removed and rebuilt.
- `blt-lab-02` and `-04` to `-10` installed and registered one at a
  time, each on its first attempt - the retry was never needed, which
  fits the earlier failure having been three registrations at once.
- All ten containers have the agent's services running without any
  manual step, so the entrypoint's start-without-systemd change works on
  fresh containers.
- Each new client got `blt-lab-data` -> `/data` under `Standard Plan`.

### Noise round across everything, 2026-10-06

`lab/20-noise.sh`: 14 fulls started together (jobs 13-26). All ten
container clients Completed, including the eight being backed up for the
first time; `etc` and `drivers` Completed; `missing` Failed.

The suspend / resume / kill on `fonts` ended as **Committed** this time,
not Killed: Commvault keeps what a killed job had already written and
reports it that way when there is something to keep. Same script, a
fourth distinct ending. blt already had `Committed` in its status table
(finished, not active), so it was stored correctly.

blt after the following collection: 26 jobs - 20 completed, 3 failed,
2 killed, 1 committed - across 11 clients.

Still to do: an incremental round, `--loop`, a container restart, and
teardown.
