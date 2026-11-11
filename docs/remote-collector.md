# Remote collectors: collecting from networks blt cannot reach

A **remote collector** is a blt worker on another machine - a tools
server inside a production network, a server in another region - that
collects from the sources only it can reach. It runs the same worker
image as blt's own, and sends what it collects to the same blt.

```
blt's machine (desktop, or the cluster)          tools server in the production network
  Prefect API  127.0.0.1:4200  <---------+          +-- blt-collector-uk (podman, host network)
  blt API      127.0.0.1:8088  <-------+ |          |     asks Prefect for work: 127.0.0.1:14200
                                       | |  ssh -R  |     posts what it collects: 127.0.0.1:18088
  ssh (from this machine) =============+=+=========>+
                                                    +--> NetBackup / Commvault / Rubrik it can reach
```

## How the work gets to it

Prefect workers **pull**: each one asks Prefect, every few seconds, for
runs waiting in its **work queue**. blt uses one work pool, `blt`:

- blt's own worker takes only the `default` queue.
- A remote collector named `uk` takes only the `uk` queue. It makes the
  queue when it starts.
- Each source names its collector (Administration -> Sources ->
  Collector; empty means blt's own). When a schedule fires, the parent
  flow starts one run per source **in that source's collector's queue**.
  The same goes for a check, a CA fetch, and Run now for one source.

So one schedule still covers every source, and each source's run happens
where that source can be reached.

**Regions.** Make one collector per region (`omaha`, `uk`, `asia`) and
point each region's sources at it. Several machines may run the same
collector, for example two tools servers in the UK. They share its queue,
and each run goes to one of them.

**One run per source, wherever it runs.** A source's run that finds the
previous one still going skips ("already running"), and two runs never
renew one Commvault token pair at once. Both locks are held in Prefect
(`blt-lock-<source>.<kind>` global concurrency limits of one slot,
blt.flows.locks), so they hold across machines - and across pods, for a
location's cluster that runs each flow run in its own. A run holds its
lock under a lease it renews as it goes; if its machine or pod dies, the
lock is free again within two minutes.

**When a collector is away.** If a run is still waiting at the parent's
deadline (`wait_minutes`), it is **cancelled** and reported: "not picked
up ... is its collector running?". Without that, runs would pile up while
a tunnel is down and all start at once when it came back. Nothing is
lost: the next run collects everything since the last one that worked.
The Collectors panel shows the collector as **offline**.

## Setting one up (blt on a podman desktop)

From the machine blt runs on (in WSL on the work desktop), in the repo:

```bash
# 1. Copy the worker image over (only when it changed) and start the collector.
deploy/collector.sh push tools-server uk

# 2. Open the tunnel and leave it open. Ctrl-C closes it.
deploy/collector.sh tunnel tools-server

# 3. Is it there? Container, tunnel as the collector sees it, last log lines.
deploy/collector.sh status tools-server uk
```

`tools-server` is anything `ssh` takes: `user@host`, or a `Host` from
`~/.ssh/config`. WSL has its own `~/.ssh`, separate from Windows.

Then, in blt: **Administration -> Sources**. `uk` appears under
Collectors (online once the tunnel is up). Set each source it should
collect to `uk`, check it, and turn collection on. A new NetBackup
primary can be given its collector when it is added.

**The tunnel in your usual ssh session instead.** If you are connected to
the tools server most of the day anyway, put the forwards in its `Host`
entry, and every session brings the tunnel up:

```bash
deploy/collector.sh ssh-config     # prints the RemoteForward lines to paste
```

A second session to the same host at the same time only warns that the
ports are taken. The first session's tunnel keeps working.

### Keeping the tunnel up

`tunnel` lasts as long as its terminal: close it, let the laptop sleep, or
shut WSL down, and the collector goes offline. Its runs are then cancelled
unstarted and reported, and that region collects nothing until the tunnel
is back. To have it come up by itself and come back after every drop:

```bash
deploy/collector.sh keep-tunnel tools-server   # a systemd user service, started now and at every boot
systemctl --user status blt-tunnel-tools-server.service
deploy/collector.sh drop-tunnel tools-server   # stop it and remove it
```

The service restarts the tunnel 15 seconds after it ends, however often.
ssh notices a dead connection within about 90 seconds (keepalives every
30), and a tunnel that could not claim its ports ends rather than looking
up while forwarding nothing. For it to work unattended:

- **ssh without a prompt.** The service never answers one (BatchMode): a
  key loaded in `ssh-agent` for your user, or a key kept only for this
  with no passphrase. On the tools server, that key may be limited to the
  tunnel in `authorized_keys`:
  `restrict,port-forwarding,command="/bin/false" ssh-ed25519 AAAA...`.
- **The service running with nobody logged in:** once,
  `sudo loginctl enable-linger $USER`.
- **In WSL**, systemd on: `/etc/wsl.conf` with `[boot]` `systemd=true`,
  then `wsl --shutdown` from Windows. And WSL itself kept running: Windows
  stops a WSL distribution a while after its last window closes. Either
  keep a WSL window open, or start it at sign-in from Windows Task
  Scheduler with `wsl.exe -d <distro> --exec sleep infinity`.

`journalctl --user -u blt-tunnel-tools-server.service` shows each drop and
restart.

**After upgrading blt** (`./deploy/up.sh` builds a new worker image), run
`push` again so the collector runs the same version.

**Removing it:** `deploy/collector.sh stop tools-server uk` removes the
container and its settings file. Point its sources at another collector
first.

### What the tools server needs

- Podman, rootless is fine, under the account you ssh in as.
- `AllowTcpForwarding` not set to `no` in its sshd_config. The default
  allows it.
- Nothing listening on 127.0.0.1:14200 or 127.0.0.1:18088 there. Change the
  ports with `BLT_COLLECTOR_PREFECT_PORT` and `BLT_COLLECTOR_API_PORT` in
  config/blt.env, then `push` again.
- Network access to the backup servers it collects from, as the tools
  server itself has.

### What it holds

`~/.config/blt/collector-<name>.env` (mode 600) holds Prefect's password
and blt's API key. With those, the collector reads the source settings it
needs from Prefect, credentials included. They are sent over ssh's input,
never on a command line. The tools server therefore holds the keys to
every source in Prefect, so treat it as you would blt's own machine.
`stop` deletes the file.

## When blt itself moves to the tools server

`deploy/move.sh HOST --adopt-collector NAME` moves blt to the server the
collector runs on and points the collector at it directly: no tunnel.
docs/moving.md has the steps.

## blt on the cluster, and location clusters

On the cluster, Prefect and the API are https ingresses (prefect.blt...,
api.blt...), so a collector that can reach them needs no tunnel. Run the
image with `BLT_COLLECTOR`, `PREFECT_API_URL`, `PREFECT_API_AUTH_STRING`,
`BLT_API_URL` and `BLT_API_KEY` pointing there (and `SSL_CERT_FILE` /
`PREFECT_API_SSL_CERT_FILE` for core's CA, unless its certificate is
publicly signed).

The direction for each location is a small Kubernetes cluster of its own
running the collector, with core's ingresses its only way to blt:
yoki-blt-infra `docs/location-cluster.md` and
`clusters/location/blt-collector` (two replicas, now that the locks are in
Prefect). The step after: a Prefect Kubernetes work pool per location, a
pod per flow run.

## Known limits

- A collector stopped in the middle of a run leaves that run in Running
  until `blt-crash-silent-runs` marks it Crashed (5 minutes without a
  heartbeat; docs/admin.md, "Stuck runs"), and its lock until its lease
  runs out.
- The collector uses blt's one API key. Keys scoped to collecting come
  with Zitadel.
