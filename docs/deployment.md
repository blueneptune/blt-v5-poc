# blt: deployment and migration strategy

> How blt is packaged, where it runs, and how an installation moves from
> one host on podman to the Kubernetes platform in
> [vision.md](vision.md) - and back, if it has to.
>
> First recorded 2026-10-28, after the lab cutover to the core cluster.

**Contents:**
[1. The idea](#1-the-idea) ·
[2. The contract](#2-the-contract) ·
[3. The services](#3-the-services) ·
[4. Three ways to run it](#4-three-ways-to-run-it) ·
[5. Releases and promotion](#5-releases-and-promotion) ·
[6. Migrating from podman to Kubernetes](#6-migrating-from-podman-to-kubernetes) ·
[7. What makes it robust](#7-what-makes-it-robust) ·
[8. How each piece scales](#8-how-each-piece-scales) ·
[9. The work, in order](#9-the-work-in-order)

---

## 1. The idea

**One blt, built once, run anywhere.** The same images, the same
configuration and the same database migrations run on one podman host
and on a Kubernetes cluster. What changes between them is only *how the
containers are started and kept alive* - never the application.

- **Single host (podman)** is the reference install: self-contained, one
  command, no platform to run. Right for one team next to its backup
  servers, a laptop, a WSL box, or a customer install.
- **Kubernetes** is the scale-out install: high availability, many
  sources collected in parallel, several zones, shared platform services
  (identity, registry, certificates, logs). Right for running blt as a
  service across many estates.

An installation can start on podman and move to Kubernetes when it
needs to, with a written runbook (section 6) and no code changes. The
move back must work too: it is the test that blt has not come to depend
on Kubernetes.

## 2. The contract

What every blt service promises, so that it does not care where it runs.
A change that breaks one of these is a bug, whichever install it helps.

| Rule | In practice |
|---|---|
| **One image per service, built once** | Built by CI from a tagged commit; the same image goes to the lab, then production. Never rebuilt on a host. |
| **Configuration from the environment** | Everything a service needs is an environment variable or a secret file. No hostnames, paths or ports written into the code. `config/blt.env` on podman, Secrets and ConfigMaps on Kubernetes. |
| **State lives in Postgres** | Services keep nothing on local disk that matters. Source credentials live in Prefect blocks (also in Postgres). Any container can be replaced at any time. |
| **The schema moves forward by itself** | Alembic migrations (`blt-migrate`), run once per release before the new version serves. Every migration works against the previous release's code (expand, then contract a release later). |
| **Find each other by address, not by platform** | `BLT_DATABASE_URL`, `PREFECT_API_URL`, `BLT_API_URL`. No Kubernetes API calls, no service discovery library. |
| **Say when you are healthy** | `/healthz` (alive) and a readiness check (can reach the database) on every service with a port. |
| **Logs to stdout** | One line per event, structured. The platform collects them (journald on podman, Loki on Kubernetes). |
| **Stop cleanly** | On SIGTERM: finish or hand back the current work, then exit, within 30 seconds. |
| **Platform services are optional** | Identity, registry, log collection and certificates are things blt *uses* through standard interfaces (OIDC, an image name, stdout, a CA file), not things it is built around. |

## 3. The services

blt is split where things **scale differently or change for different
reasons** - not one service per feature. Collectors for each product are
not services: they are **flows**, packaged in worker images, run as
Prefect flow runs.

| Service | Image | State | Runs as | Notes |
|---|---|---|---|---|
| **API** | `blt-api` | none | 1+ replicas | FastAPI; backend for the UI, ingress for collected data |
| **UI** | `blt-ui` *(Phase 1)* | none | 1+ replicas | SvelteKit; talks only to the API |
| **Prefect server** | `blt-prefect-server` | none (Postgres) | 1 replica | schedules, retries, run history; blocks hold source credentials |
| **Worker** | `blt-prefect-worker` | none | 1 per work pool | starts flow runs: in-process on podman, one pod per run on Kubernetes |
| **Migrations** | `blt-api` (`blt-migrate`) | - | once per release | a one-shot job before the API starts |
| **Postgres** | upstream | **all of it** | 1 on podman; CloudNativePG cluster on Kubernetes | a database per service: `blt`, `prefect`, later `zitadel`, `grafana` |

Platform services, shared by everything in a cluster and **not part of
blt's release**:

| | Single host | Kubernetes |
|---|---|---|
| Ingress and TLS | ports on the host; optional reverse proxy | Traefik + cert-manager |
| Identity | API key (today); a Zitadel container when the UI arrives | Zitadel |
| Images | built locally or loaded from a release bundle | Harbor |
| Logs and dashboards | journald | Loki + Grafana |
| Backups | nightly `pg_dump` to a mounted folder | CloudNativePG to object storage |

## 4. Three ways to run it

### 4.1 Single host - podman under systemd

What `deploy/up.sh` does today, made into a proper install:

- **Quadlet units** (`~/.config/containers/systemd/`) instead of the
  start script: the pod starts at boot, restarts on failure, and
  `systemctl status blt` tells the truth. A `.kube` quadlet can play
  `deploy/kube/blt.yaml` - so the single host already runs a Kubernetes
  manifest, through podman.
- **Images by release tag**, pulled from the registry or loaded from a
  release bundle (`podman load`) where the host cannot reach one.
- **Updates**: change the tag in one file, `systemctl restart blt`; the
  migration container runs first.
- **Backups**: a systemd timer runs `pg_dump` nightly, keeps 14 -
  `deploy/backup.sh install-timer`, in place since 2026.10.31.
- Works in WSL with systemd turned on (`/etc/wsl.conf`).

### 4.2 Core cluster - Kubernetes

What runs in the lab today (`yoki-blt-infra/clusters/core`), finished:

- Images from **Harbor**, by release tag (not `imagePullPolicy: Never`).
- **GitOps**: the cluster follows `yoki-blt-infra` - a merge to its main
  is a deployment. Rancher's **Fleet** is already installed and does
  this without adding anything.
- Migrations as a **Job** (or init container) per release; the API then
  runs 2 replicas with a PodDisruptionBudget.
- **Prefect Kubernetes work pool**: every flow run its own pod, with its
  own CPU and memory limits - one stuck CommCell cannot starve another.
- Postgres backups to object storage, and a restore tested every month.

### 4.3 Core plus access zones

The end state in [vision.md](vision.md) section 3: a small cluster per
access zone running only workers, which reach out to core's Prefect and
API. Nothing in blt changes for this - a worker image, a work pool name
and the core's addresses. Comes after 4.2 is solid.

## 5. Releases and promotion

A release is a tag on `main`, named for the day it is deployed
(`YYYY.MM.DD`; the README's Releases section). The weekly train
(`YYYY.MM.Wnn`, merged Thursday, tested Friday, released Monday) is
paused while blt is being built, and comes back when others depend on
it. Either way, deployment works the same:

```
tag 2026.11.09 ──> CI builds blt-api, blt-prefect-server, blt-prefect-worker
                   tagged 2026.11.09 (and the commit) ──> Harbor
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          ▼                         ▼                         ▼
  lab cluster: bump tag      production cluster:        single hosts:
  in yoki-blt-infra,         same tag, after the        release bundle (tar)
  Fleet applies              lab has run it             + `systemctl restart blt`
```

- An image tag is **never reused**; rolling back is setting the previous
  tag. Migrations must allow that (the expand/contract rule above).
- The infra repo records *what runs where*; its history is the
  deployment history.
- Release notes say if a release needs anything beyond a tag bump (a new
  secret, a manual step).

## 6. Migrating from podman to Kubernetes

The runbook, written from the lab cutover on 2026-10-27. Expect about
an hour of collection paused; nothing is collected twice or lost, because
the next delta run catches up from the watermark.

**Before the day**

1. The cluster's platform is up: ingress, certificates, Longhorn,
   CloudNativePG, Harbor - and the checks in section 7 pass.
2. The release running on the host is the release going to the cluster
   (same image tag). Upgrade the host first if not - never migrate and
   upgrade in one step.
3. blt is deployed on the cluster with **no worker** (API, Prefect
   server, empty Postgres) and answers on its addresses.

**On the day**

4. **Stop collecting on the host**: stop the worker (`podman stop
   blt-prefect-worker`). Nothing else changes yet - the host's API keeps
   serving reads.
5. **Copy the data**: `pg_dump` the `blt` and `prefect` databases from
   the host, restore them into the cluster's Postgres (or let
   CloudNativePG import them directly from the host -
   `bootstrap.initdb.import`). Compare row counts of `job` and
   `collection_run`.
6. **Credentials**: they are in Prefect blocks, so they came with the
   `prefect` database. Each token pair has **one home** - the host's
   copies must already be blank (`BLT_TOKENS_IN=prefect`), or are blanked
   now. Never let both sides renew the same token.
7. **Start the cluster's worker** and run one collection by hand per
   source; check it completes.
8. **Point people and tools at the cluster**: DNS names, bookmarks,
   `source scripts/use-cluster.sh`.

**After**

9. Keep the host's containers (stopped worker, read-only) for a week as
   the fallback, then remove them and keep the last dump.

**Rolling back** is the same steps the other way: stop the cluster's
worker, dump, restore on the host, start the host's worker.

## 7. What makes it robust

Most of what can go wrong on Kubernetes is underneath blt. The lab
found these the hard way; each is a check before a cluster carries
production:

| Area | Check | Learned |
|---|---|---|
| **Time** | Every node synced by chrony; the hypervisor's own clock right; VMware Tools time sync off (including one-off syncs on snapshot and vMotion) | 2026-10-28: a host 7 h fast jumped one node; every token it issued was refused when the clock came back - networking, storage and a database primary on that node stopped talking to the cluster |
| **DNS** | Every name blt uses resolves the same from every DNS server the nodes use, from inside a pod | 2026-10-28: one of two servers did not know `cv-toaster`; half the collections failed |
| **Credentials** | One home per token; nothing else may renew it | 2026-10-27: a stale refresh token revoked the live one |
| **Database** | 3 instances on 3 nodes; backups off the cluster; a restore drill | failover worked in 3 minutes when a primary's node broke |
| **Capacity** | Longhorn: free space for one more copy of the largest volume on every node; requests and limits on every pod | 2026-10-28: two nodes were 3 GiB from full |
| **Certificates** | Expiry monitored (cert-manager renews; the root CA and the cluster's own certs are not renewed by it) | |
| **Disruption** | PodDisruptionBudgets so a node drain or upgrade never takes the last API or database replica | |
| **Seeing it** | Logs in Loki, alerts on: collection failed twice in a row, no successful collection in 3 hours, Postgres not healthy, disk over 80 % | |

## 8. How each piece scales

| Piece | Scales by | Limit to watch |
|---|---|---|
| API, UI | more replicas (by hand, later on CPU) | database connections - use a pooler (CNPG's PgBouncer) past a handful of replicas |
| Collection | more flow runs at once: a pod per run, concurrency limits per work pool and **per source** | the source's API, not ours - a CommServe should see at most one or two blt runs at a time |
| Zones | another access-zone cluster with its own work pool | none in blt; network rules and credentials per zone |
| Prefect server | one replica until needed; then its messaging moves to Redis and the API runs several | event volume; run history retention |
| Postgres | bigger instances first; reads (reports, Grafana) from the `-ro` replicas | one primary for writes - fine for the scale in the vision |

## 9. The work, in order

Each step is usable on its own. Steps 1-3 make blt portable; 4-6 make
the cluster production-grade; 7-8 grow it.

| # | Step | Done when |
|---|---|---|
| 1 | **Contract gaps**: readiness checks, structured logs, SIGTERM handling, migrations as their own step | every row of section 2 is true and tested |
| 2 | **Images by release** built in CI, pushed to Harbor, plus a bundle per release | a release tag produces three images and a tar, no hand builds |
| 3 | **Single-host install**: quadlets, backup timer, install doc | a clean Rocky or WSL host runs blt from the doc in under 30 minutes |
| 4 | **Cluster pulls from Harbor**, Fleet applies `yoki-blt-infra` | a tag bump in Git is the whole deployment |
| 5 | **Backups and restore drill** for CloudNativePG; capacity and time checks | a restore into a scratch namespace matches production |
| 6 | **Observability**: Loki, Grafana, the alerts in section 7 | a failed collection reaches someone without them looking |
| 7 | **Kubernetes work pool**: a pod per flow run, per-source limits | ten CommCells collect in parallel without one slowing another |
| 8 | **First access zone** | a worker outside core collects through its zone |

Then the vision's phases continue on top: Zitadel and the UI (Phase 1),
the data foundation (Phase 2), and so on.
