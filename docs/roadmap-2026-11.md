# What comes next (agreed 2026-11-05)

In this order. Each step is usable on its own; later steps build on the
earlier ones. Fits [vision.md](vision.md) phases 4 and 5.

## 1. Live SDK tests, and documentation from real runs

**Goal:** every SDK call proven on real equipment, like for like with its
mocked test, and documented with a real example of it in use and what it
returned. Then the SDKs grow towards complete.

- A `tests/live/` suite in each SDK, `pytest -m live`, run on eth00dev1
  (GitHub cannot reach the lab). The fast mocked tests stay in CI.
- Each live test makes the real call, checks the answer parses, and
  checks it has the shape the mocked test assumes - the check that would
  have caught the NetBackup `/config/hosts` shape before blt did.
- A recorder captures every request and answer; masked (the SDK repos are
  public), they become `docs/live/` pages: the code, the request, the
  answer, the result, the product version, the date.
- **Complete, measurably.** NetBackup publishes its API description on
  the primary (`https://<primary>/api-docs/`): 733 operations in 10.2.
  The SDK gets a generated layer with one call per operation, and the
  hand-written calls blt uses on top. Coverage is reported against the
  spec, both "wrapped" and "proven live"; 10.5 and 11.0 are diffed
  against 10.2 when those primaries exist. Commvault the same way where
  it publishes a description.
- **Lab:** cv-toaster (Commvault) and eth00bck01 (NetBackup 10.2) are
  long-lived; each run makes its own `bltlive-<run>` objects - clients,
  policies, plans, storage, backups - and removes them at the end. The
  user's own objects are left alone. Rubrik has no lab: read-only live
  tests against the work account.

## 2. Storage monitoring

**Commvault built 2026-11-05 ([storage.md](storage.md)); NetBackup next.**

**Goal:** storage use by environment and kind of storage, every hour,
with trends; a **Storage** page in the side panel.

The breakdown follows each product's own hierarchy:

| Product | Hierarchy |
|---|---|
| Commvault | CommServe -> MediaAgent -> storage pool / disk library (one MediaAgent can hold several; some look alike but are separate libraries) -> mount paths |
| NetBackup | primary -> media server -> storage server -> disk pool (AdvancedDisk, MSDP, BasicDisk, MSDP-C cloud, Data Domain) -> storage unit |
| Rubrik | account -> cluster (capacity) ; archive locations |
| Veeam | server -> repository / scale-out extents |

- `common.storage`: one row per storage target - source, the path down
  the hierarchy (MediaAgent or media server, storage server), its kind
  (disk, dedupe, cloud object, tape, cluster), product key.
- `common.storage_sample`: capacity, used, free (and, where the product
  says, logical vs physical and dedupe ratio) every hour - a Prefect flow
  per source. A few hundred targets give about two million rows a year.
- UI: by source, by media server / MediaAgent, by kind; use over time;
  "full in about N days" from the trend.

## 3. Environment change log

**Goal:** every change to what protects what - Commvault plans and
storage policies, NetBackup policies, schedules and SLPs, Rubrik SLA
domains, Veeam jobs - recorded as a new version with a diff.

- Collected by a flow, normalised and hashed; a change makes a new
  version and a `common.change_event` (created / modified / deleted).
- Later: who made it, from each product's audit log.
- UI: a **Changes** page in the side panel, and each policy's history.

## 4. Later: management, then work orders

Browse and search the configuration (read-only) first. Then work orders:
queued for approval, a ServiceNow change raised with the documentation,
executed through the SDK calls the live tests have proven, recorded and
closed. Needs sign-in (Zitadel) first, so approvals have real people
behind them.
