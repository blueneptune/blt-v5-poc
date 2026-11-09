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

## blt on the cluster

Nothing about the collector changes except where it reaches Prefect and
the API. On the cluster these are https ingresses (prefect.blt...,
api.blt...), so a collector that can reach them needs no tunnel. Run the
image with `BLT_COLLECTOR`, `PREFECT_API_URL`, `PREFECT_API_AUTH_STRING`,
`BLT_API_URL` and `BLT_API_KEY` pointing there. `collector.sh` does the
desktop case only, for now.

## Known limits

- **Two machines on one collector can overlap on one source** if one
  source's run outlasts its interval, because the "already running" lock
  is per machine. The data stays right, since every write is an upsert;
  only the work is doubled. A Prefect-held lock per source would fix
  this (TODO.md).
- A collector stopped in the middle of a run leaves that run showing
  Running in Prefect until someone cancels it.
- The collector uses blt's one API key. Keys scoped to collecting come
  with Zitadel.
