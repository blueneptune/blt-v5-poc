# blt: platform vision

> Where blt is going, recorded from the owner's description and the
> decisions made since. Keep it current: when something is decided, move
> it from *Open questions* to *Decisions*.
>
> First recorded 2026-10-22.

**Contents:**
[1. Purpose](#1-purpose) ·
[2. Principles](#2-principles) ·
[3. Platform](#3-platform) ·
[4. Capabilities](#4-capabilities) ·
[5. Roadmap](#5-roadmap) ·
[6. Decisions](#6-decisions) ·
[7. Open questions](#7-open-questions) ·
[8. Where it stands](#8-where-it-stands)

---

## 1. Purpose

blt is a monitoring and management tool for the backup products in use -
**Commvault, Veeam, NetBackup and Rubrik** - with one UI as the single
pane of glass. Each product has its own UI; blt replaces moving between
them with one consistent experience.

Around that sits the **asset refinery**: blt matches every object it
discovers - in backups, in VMware, in ServiceNow - to the real asset it
belongs to, using every data source as a possible marker, with
ServiceNow as the source of truth everything is matched against.

## 2. Principles

| | |
|---|---|
| **One pane of glass** | Every capability lives in blt's UI, behind one login, with one look. |
| **Connections start in the access zone** | Core never calls out. Workers fetch their work from core and post results to it. |
| **Read first, change later** | Collect and show before anything is changed on a product; changes come with approvals and a ticket. |
| **Everything has history** | Assets, configuration and manual decisions keep every version, with who and why. (Jobs keep their latest state.) |
| **Rules are data** | Matching, audits and SLA checks are rules evaluated against collected data, producing findings. |
| **Free and open** | Every component is open source and usable in an enterprise without a licence. |

---

## 3. Platform

### 3.1 Zones

```
customer zones               access zones                     core zone
(where the data lives)       (where work runs)                (where blt lives)

backup servers,     <──────  Prefect workers:      ──────>   UI, API, Prefect server,
vCenters,                    US1, UK1, ...                    database, identity, ...
ServiceNow                   (CORE can also act as one)
```

| Zone | Can reach | Cannot reach |
|---|---|---|
| **core** | - | access, customer |
| **access** | customer, core | - |
| **customer** | access | core |

Once an access zone's worker is running, everything starts there:
polling core for work, collecting from customer systems, posting results
back to core.

### 3.2 Core zone

One Kubernetes cluster: **3 Rocky Linux servers, RKE2.**

| Component | Choice | Role |
|---|---|---|
| Entry point | reverse proxy / ingress | the one way in for users and workers |
| UI | SvelteKit | monitoring, management and administration |
| API | FastAPI | backend for the UI; ingress for collected data |
| Identity | **Zitadel**, pending a licence check - Keycloak if AGPL is not allowed (7.1) | local users, SSO (Okta, Google), roles, service accounts - managed from blt's UI through its API |
| Orchestration | Prefect server | schedules, retries, run history, events |
| Core worker | Prefect worker | tasks that run in core |
| Database | Postgres - one cluster | see 3.5 |
| Certificates | smallstep CA | trust for workers in other zones (mTLS, later) |
| Registry | container registry (Harbor) | images for every zone |
| Logs | Loki | |
| Dashboards | Grafana | |
| Reports | Apache Superset | if Grafana is not enough |
| Cache | Redis | if caching is needed |

### 3.3 Access zones

One small Kubernetes cluster per zone - **US1, UK1, ...** - running
almost nothing but Prefect workers. Core can act as an access zone too
(the **CORE** work pool) for work it can do itself.

- Collects from backup products, polls vCenter and ServiceNow, handles
  events.
- **Each collection runs in its own container**: Prefect's Kubernetes
  work pool turns every flow run into its own pod.
- **RKE2**, as in core: one set of scripts, one upgrade path. Sizing is
  open (7.2).

### 3.4 Customer zones

Where the data lives and what blt collects from - backup servers,
vCenters, ServiceNow. Reachable only from an access zone.

### 3.5 Data

**One Postgres cluster** (CloudNativePG), with a **separate database per
service**, each with its own login:

| Database | Used by |
|---|---|
| `blt` | blt's API, collectors and flows |
| `prefect` | Prefect server |
| `zitadel` (or `keycloak`) | identity |
| `grafana`, `superset` | dashboards and reports, when added |

Inside `blt`, **one schema per data source type** for that product's own
data, and a common **`blt` schema** that ties them together:

```
blt database
├── commvault     Commvault's data, in its own shape
├── netbackup
├── veeam
├── rubrik
├── vmware
├── servicenow
└── blt           sources, blt job ids, assets, sightings, links,
                  history, rules and findings - what the UI reads
```

Product tables point up to `blt`; `blt` never points into a product
schema. Words are defined in [glossary.md](glossary.md).

---

## 4. Capabilities

Each area is a section of the UI. **Phase** refers to the roadmap (5).

### 4.1 Sign-in, users and roles - *Phase 1*

- Sign-in is the first point of entry: a local admin, local users, and
  SSO through Okta, Google or others.
- Users, roles and SSO connections are managed **from blt's UI**, not
  the identity provider's own: blt's backend calls its management API
  with a service account that may only manage users, groups, roles and
  identity providers. The provider's own console stays as an emergency
  fallback.
- **Roles** decide what each user may see and administer.
- *Later:* roles **scoped to customers** - e.g. GDPR customers whose
  backup data only UK-based engineers may see. *Organizations* are the
  likely building block - a core concept in Zitadel, a newer feature in
  Keycloak.

### 4.2 Data sources - *Phase 2*

Adding a data source:

- **Type** from a drop-down: Commvault (CommCell), Veeam, NetBackup,
  Rubrik, VMware, ServiceNow.
- **Host**, and **one or more authentication methods** - tried in order,
  for failover and credential rotation.
- **Work pool** that collects it: US1, UK1, CORE, ...
- **Frequency per module**: jobs, policies, catalog, storage, tape
  devices, events, ...

Settings and credentials are kept in **Prefect blocks**, because every
collection runs through Prefect.

### 4.3 Work pools - *Phase 2*

- Create a work pool in the UI.
- blt produces a **deployment bundle** for that zone's cluster: the
  worker's manifests, its credentials, core's CA certificate.
- The bundle is applied in the access zone; the worker then connects
  back to core. Core never reaches into the zone.

### 4.4 Jobs - *Phase 2*

- Every job in one place: backup jobs from every product, Prefect runs,
  VMware tasks.
- **blt's own job id** links a job across tools and schemas.
- Filter by product, state, client, ...; **save filters** per user.
- Failed jobs stand out in colour, with their **ServiceNow event**
  alongside to drill into (once events are written - 4.8).
- **Notes** on a job; **follow** a job to be notified of its updates.

### 4.5 Inventory and the asset refinery - *Phase 3*

- An **asset** is anything discovered by any data source, linked inside
  blt.
- A **rules engine** merges the data sources' views of each asset;
  sources can be ranked as source of truth.
- **Manual links** when matching gets it wrong.
- Every change - matches, state changes, manual links - kept as
  **history**.
- Assets are **searchable**. An asset's page shows:
  - every data source it maps to
  - the backup jobs it appears in, and its backup history
  - standard checks - is it backed up, is it within its SLA
  - tickets, alerts and events about it, each clickable
  - notes

### 4.6 Configuration management and audits - *Phase 4*

- Collect the **configuration** of every backup environment, and keep
  every change as a **new version** - e.g. a NetBackup policy changed
  outside blt at 1 pm is recorded, and its history shows both versions.
- **Audit** configuration regularly against business rules, for
  example:
  - a policy should keep monthly backups for 7 years - does it?
  - a monthly backup on the first Friday, with no Friday backup window
  - schedules with fixed dates that have run out
  - names that break the naming standard
- **Notes** on settings.

### 4.7 Changes with approval - *Phase 5*

Create, modify and delete settings on a backup product from blt:

1. Users flagged `requires_approver` wait for a user flagged as
   approver; others run straight away.
2. A **ServiceNow change** is raised with the standard information.
3. The change moves to *Implement*.
4. blt makes the change.
5. The result is recorded; the change ticket is updated and closed.
6. The history stays in blt, with **rollback** where the setting allows.

### 4.8 ServiceNow

| | When |
|---|---|
| **Read** - CMDB for inventory and source of truth | *Phase 3* |
| **Write events** - for failed backups | later |
| **Write changes** - for approved changes (4.7) | *Phase 5* |

### 4.9 Notifications

One notification service - email, Teams, Slack - for followed jobs, SLA
breaches and failed collections.

### 4.10 Reporting and observability

- **Reporting:** Grafana dashboards first; Superset when a report needs
  it.
- **Observability:** the backup servers, blt's own services, storage
  use - through Loki and Grafana.

### 4.11 Everyday comforts

- A **feature request** button at the top of every page.
- **Themes** (light and dark) and **accessibility** features.

---

## 5. Roadmap

Each phase is usable on its own and builds on the one before.

| Phase | Delivers |
|---|---|
| **0 - Platform** | core cluster; Postgres, identity provider, Prefect, registry, ingress; one access zone; Loki and Grafana |
| **1 - Front door** | the UI shell: sign-in, users and roles, themes |
| **2 - Data foundation** | product schemas under `blt`; data sources and work pools in the UI; blt job ids; the jobs page |
| **3 - Inventory** | VMware and ServiceNow (read) as sources; assets, matching rules, manual links, history; search and the asset page |
| **4 - Configuration** | versioned configuration; audit rules and findings |
| **5 - Changes** | approvals, ServiceNow changes, execution, rollback |
| *Throughout* | notifications, reporting, observability, more products (Veeam, NetBackup, Rubrik) |

## 6. Decisions

| Date | Decision |
|---|---|
| 2026-10-15 | Prefect for orchestration; per-source flow runs, in parallel |
| 2026-10-15 | Kubernetes: RKE2 on Rocky Linux 9 |
| 2026-10-20 | The name stays **blt** |
| 2026-10-22 | Zones are **core / access / customer**, with several access zones |
| 2026-10-22 | Work pools: blt produces a bundle, applied in the access zone, which then connects back |
| 2026-10-22 | Credentials for every source in Prefect blocks in core; a worker in one zone may read another zone's blocks (revisit later) |
| 2026-10-22 | ServiceNow read-only to start; writing events and changes later |
| 2026-10-22 | One Postgres cluster, a database per service, a schema per product inside `blt` |

## 7. Open questions

1. **Identity provider: Zitadel or Keycloak.** Zitadel is preferred -
   lighter, multi-tenant by design (organizations), and already started
   for bartman - but it is **AGPL 3.0** since v3. Running it unmodified
   internally costs nothing and obliges nothing, and blt, which only
   calls its API, is unaffected; but many companies ban AGPL outright.
   *To ask legal / the open-source office:* "Can we run AGPL-3.0
   software internally, unmodified?" Yes: Zitadel. No, or unclear:
   **Keycloak** (Apache 2.0). Both speak OpenID Connect, so blt does not
   depend on the choice.
2. **Access-zone size.** Three small servers (2 vCPU / 8 GB) for full
   high availability, or one server plus one or two agents to save
   cost. Two servers protect no better than one.
3. **Customer-scoped roles** (GDPR, UK-only) - how customers are
   defined in blt, when this becomes a priority.
4. **Which product after Commvault** feeds the jobs page first.

## 8. Where it stands

*As of 2026-10-22.*

- **Commvault** collection (CommServes and Commvault Cloud) runs in
  production on Prefect: one flow run per source, in parallel,
  credentials in blocks.
- **SDKs:** Commvault (proven), Veeam and NetBackup (written, not yet
  run against a server).
- **Infrastructure:** `yoki-blt-infra` written (RKE2, kube-vip,
  cert-manager, Longhorn, CloudNativePG, Harbor plan), waiting for
  servers; a home-lab rehearsal at `*.blt.ethos.home` with a smallstep
  CA.
- **Next:** Commvault's tables into a `commvault` schema (2026.11.W01),
  then the `blt` schema.
