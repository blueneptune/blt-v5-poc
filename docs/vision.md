# blt: platform vision

**Recorded 2026-10-22** from the owner's description of where blt is
going. Parts 1-4 are that description, organised but not changed in
substance. Part 5 holds open questions, part 6 suggestions - those are
proposals, not decisions. Update this file as decisions are made.

## 1. Goal

A monitoring and management tool for several backup products -
**Commvault, Veeam, NetBackup, Rubrik** - with one UI as the single pane
of glass, so the experience is consistent and seamless instead of a
different UI per product.

**Asset refinery.** blt's asset and inventory matching. It takes data
from the backup products and matches assets using other data sources
such as **ServiceNow** and **VMware**. Every data source is a potential
marker for matching. Data sources can be prioritised as source of truth;
ServiceNow is treated as the source of truth that all collected data is
matched against. *Further investigation is required.*

## 2. Platform: three zones

| Zone | Can talk to | Cannot talk to |
|---|---|---|
| **core** | - | access, customer |
| **access** | customer, core | - |
| **customer** | access | core |

Once a worker is running in an access zone, everything starts from
there: data collection, posting results to core, and polling the Prefect
server for work.

### Core zone

Maintains the core application and its supporting infrastructure. Three
Rocky Linux servers running Rancher RKE2.

- a reverse proxy as the entry point for the application
- a SvelteKit UI for monitoring and managing backup-tool tasks,
  administration included
- a FastAPI server: backend for the UI, and ingress for collected data
- **authentik** for identity and access management, tightly integrated
  with the UI
- a Prefect server for task and event orchestration
- a Prefect worker for core-based tasks and events
- a Postgres database where everything is stored; any of these that
  needs a database uses it
- a smallstep CA, for future certificate trust with workers in other
  zones
- a container registry that the other zones pull from
- Loki for logs
- Grafana for graphs
- Apache Superset, if required, for reports
- Redis, for potential caching

### Access zones

Run the worker pods that execute non-core tasks and event handling,
including but not limited to backup-system data collection, vCenter
polling and ServiceNow polling.

- The main, and basically only, pods here are Prefect workers.
- Each collection should spin up a dedicated container for its workload.
- There may be several access zones - for example **US**, **UK** and
  **CORE** (core can act as an access zone for certain tasks).
- Open: three Rocky servers per access zone, or two smaller ones? RKE2,
  or would K3s be simpler? (See part 5.)

### Customer zones

Where the data lives, and what blt collects from. An access zone may
also hold some data.

## 3. The UI

Testing so far has used manual CLI collection, then Prefect; the next
iteration is a full UI.

### Authentication

The first point of entry. authentik handles a local admin and local
users, and SSO integrations such as Okta or Google can be configured.

### Data sources (administration)

- Source types in a drop-down when adding one: Commvault (CommCell),
  Veeam, NetBackup, Rubrik, ServiceNow, VMware.
- Which **work pool** the collection runs on: UK1, US1, CORE, ...
- **How often** each module is collected: jobs, policies, catalog data,
  storage, tape devices, events, ...
- A **host** and a **method of authentication** - more than one per
  source, for fallback on authentication failures and for rotation.
- All work runs through the Prefect server and workers, so source
  settings and credentials are expected to live in **Prefect blocks**.

### Work pools (administration)

Work pools will be deployed in several locations. They should be
configurable here, including setting one up on its Kubernetes cluster,
as a user-friendly experience.

### Users and roles (administration)

- Manage local accounts and SSO integrations (e.g. Okta).
- Roles decide what each user may see, and who may administer the
  application.

### Jobs

- Jobs from every data source in one place: Prefect tasks, backup jobs,
  VMware jobs, ...
- blt carries **its own job id table**, linking jobs across tools and
  schemas.
- Filter by product, state, client, ...; **save filters** per user.
- Failed jobs shown in a different colour, with a **ServiceNow event**
  column to drill into.
- **Notes** on jobs.
- **Follow** a job: be notified of its updates by various means.

### Inventory

- All data sources merged by a clear, definable **rules engine**;
  assets are searchable.
- An asset is any object discovered by any source, and can be linked
  inside blt.
- Assets can be **linked by hand** when matching gets it wrong.
- A detailed **history** of mapping and of every change to an asset
  (state changes, manual links).
- An asset's page shows its whole history and relationships:
  - which data sources it maps to
  - the backup jobs it appears in, and its backup history
  - standard checks, e.g. backed up and not in breach of its SLA
  - tickets, alerts and events about it (e.g. a backup failed and an
    event was raised), each one clickable
  - notes

### Backup configuration management

- Collect the **configuration state** of every backup environment.
- When it changes, record the change as a **new version** - e.g. a
  NetBackup policy recorded at noon is changed outside blt by 1 pm; the
  change is recorded, and the policy's history shows it.
- **Audit** settings regularly against intended goals, for example:
  - a policy should keep monthly backups for 7 years - does it?
  - a monthly backup runs on the first Friday, but there is no Friday
    backup window - a miss
  - schedules with specific dates have run out of dates - find them,
    and make sure they will actually run
  - names follow the business naming rules; anything else is out of
    compliance and gets changed
- Later, **create, modify and delete** settings on backup products:
  1. if the user's `requires_approver` flag is set, the change waits for
     a user flagged as approver; otherwise it runs straight away
  2. after approval, a ServiceNow change is raised with the standard
     information
  3. the change is moved to Implement
  4. blt executes the change
  5. the result is recorded, and the change ticket is updated and closed
  6. the work's history is kept in blt, with rollback where the setting
     allows it
  7. notes can be recorded on settings

### Reporting

Visualisations, using tools such as Superset or Grafana.

### Observability

Monitoring the infrastructure as a whole: the servers doing the backups,
the state of blt's own services, storage use, ...

### Also

- **Feature requests** recorded from the UI, reachable at the top of
  every page.
- **Theme** selection (dark and light) and **accessibility** features.

## 4. Where it stands (2026-10-22)

- Collection from Commvault (CommServes and Commvault Cloud) works, in
  production, on Prefect: per-source flow runs in parallel, credentials
  in blocks.
- SDKs: Commvault (proven), Veeam and NetBackup (written, not yet run
  against a server).
- Next in the data model: product schemas (`commvault` first) under a
  common `blt` schema (docs/glossary.md).
- Infrastructure: `yoki-blt-infra` (RKE2 on Rocky 9) written, waiting
  for servers; a home-lab rehearsal under `*.blt.ethos.home` with a
  smallstep CA.

## 5. Open questions

1. **authentik instead of Zitadel.** Earlier plans named Zitadel. This
   description names authentik - is that now the decision? (It is
   assumed below.)
2. **"proxy" is now "access", and there can be several.** Confirmed by
   this description; the glossary and infra repository will be renamed.
3. **Access-zone size and Kubernetes flavour.** See suggestion 2.
4. **Setting up a work pool from the UI** collides with the zone rules:
   core cannot reach an access zone, so the UI cannot reach into its
   cluster. See suggestion 3.
5. **Where credentials live.** Prefect blocks are stored in Prefect's
   database in core, readable by anyone with Prefect admin access, and
   every access zone's worker can fetch every block. Acceptable, or
   should each access zone see only its own sources' credentials? See
   suggestion 5.
6. **Tenancy.** Are the customer zones different customers who must
   not see each other's data in the UI? That decides whether roles are
   scoped per customer or source.
7. **ServiceNow's role in both directions.** Reading CMDB data
   (inventory) and writing incidents and changes (events, approvals)
   need different ServiceNow access. Which comes first?
8. **Superset and Grafana together?** They overlap. See suggestion 9.

## 6. Suggestions

1. **Phase it.** In dependency order, each phase usable on its own:
   - *Platform* - core cluster, Postgres, authentik, Prefect, registry,
     reverse proxy, one access zone.
   - *Data foundation* - product schemas under `blt`; `blt.source` and
     the common `blt.job`; sources configured in the UI instead of
     `blt-prefect-import`; the jobs page.
   - *Inventory* - sightings, assets, matching rules, manual links,
     history; VMware and ServiceNow as sources; the asset page.
   - *Configuration management* - versioned configuration snapshots,
     then audit rules.
   - *Changes* - approvals, ServiceNow changes, execution, rollback.
   - Reporting and observability grow alongside from the platform phase
     on (Loki and Grafana are cheap to start early).
2. **Access zones: RKE2, sized to need.** The same distribution
   everywhere means one set of scripts, one hardening story, one way to
   upgrade - more valuable than K3s's lighter footprint. Access zones
   hold no state, so they can be smaller: three servers of 2 vCPU /
   8 GB give a highly available control plane (two give no more
   protection than one - etcd needs three for a quorum). If budget is
   tight, one server plus one or two agents works: if the server fails,
   running collections finish and new ones wait - Prefect's retries
   absorb that.
3. **Work pools from the UI: create in core, apply in the zone.** The UI
   creates the work pool in Prefect and produces a ready-to-apply
   bundle for the access cluster: the worker's manifest or Helm values,
   its credential, core's CA certificate. Someone in that zone applies
   it - or better, the access cluster runs a GitOps agent (Fleet or Argo
   CD) that pulls its configuration from core. Both keep the rule that
   connections start in the access zone.
4. **One dedicated container per collection** is Prefect's Kubernetes
   work pool: each flow run becomes a Kubernetes Job with its own pod,
   resources and logs. The worker itself stays small.
5. **Credentials.** Keep a source's non-secret settings in its block,
   and consider keeping the secrets nearer their use: per-access-zone
   secrets (Kubernetes secrets, or OpenBao/Vault) referenced by the
   block, so a US worker cannot read UK credentials and core holds no
   customer passwords. Multiple authentication methods per source fit
   either way: an ordered list, tried in turn, with the failure of each
   recorded.
6. **One Postgres server, separate databases.** blt, Prefect, authentik,
   Grafana and Superset each get their own database and role inside one
   Postgres cluster (CloudNativePG), rather than sharing one database.
   blt's own data is where the per-product schemas apply.
7. **blt's job id** is `blt.job`, with links to the product job and, when
   the job came from a Prefect run, to that flow run - so a backup job,
   the collection that found it, and the ServiceNow event raised for it
   all join up.
8. **Rules as data, evaluated by flows.** Matching rules, audit rules
   (retention, schedule windows, naming) and SLA checks are all "a rule,
   evaluated against collected data, producing findings". One rules
   engine with findings stored in blt serves inventory, audits and SLA
   breaches alike, and findings are what raise ServiceNow events.
9. **Grafana first, Superset when a report needs it.** Grafana covers
   operational dashboards and Loki's logs; Superset adds business-style
   reporting. Starting with one avoids running two before either is
   needed.
10. **authentik in front of the tools that have no login of their own.**
    authentik's proxy outpost can put the Prefect UI, Grafana and
    Superset behind the same login and roles, so admin-only tools stay
    admin-only - part of the single pane of glass.
11. **Notifications as one service.** "Follow a job", SLA breaches and
    failed collections all need email, Teams or Slack messages; one
    notification component (Prefect automations can feed it) avoids
    each feature inventing its own.
12. **Feature requests** can live in blt's own database at first, and
    later be pushed to GitHub issues automatically.
