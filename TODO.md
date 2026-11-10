# TODO

Open items, roughly in the order they matter, grouped by area. Numbers
never change, so a number in a commit or a conversation still finds its
item; finished ones move to [Done](#done) at the end, one line each.

## Next up

25. **Location clusters, what is left** (direction 2026-11-10: a small
    Kubernetes cluster per location runs its collector; see Done for what
    is built). Still to do:
    - a pod per flow run: `prefect-kubernetes` in the worker image, a
      Kubernetes work pool per location (`blt-<location>`) with its base
      job template, a Role to create Jobs, and blt routing a source's runs
      to its location's pool (yoki-blt-infra docs/location-cluster.md,
      "Two steps");
    - merge yoki-blt-infra `F/Location_collector` once a blt release with
      the Prefect-held locks is out, and stand up the first location;
    - an API key that may only collect, for collectors (with Zitadel).

30. **Policies, read only** (asked for 2026-11-10; not started - it needs
    a few answers first). Proposal: a fourth kind of collection,
    `policies`, twice daily like inventory, storing each product's
    policies as rows of `(source, kind, product key, name, summary
    fields, raw JSON, first/last seen)`, and a Policies page listing them
    per product with each one's raw record (as a job's page does):
    - Commvault: plans (`GET /V2/Plan`, each plan's storage copies and
      retention) and storage policies (`GET /V2/StoragePolicy` and each
      one's copies) - `cv.raw` has both;
    - NetBackup: policies (`/config/policies`: type, schedules, clients,
      storage) and SLPs (`/config/slps`: operations, retention, target
      storage);
    - Rubrik: SLA domains (`rsc.sla_domains`: frequencies, retention,
      archival, replication).
    To settle with the owner: which fields the page shows for each
    (retention, copies, schedule windows, SLP operations); whether
    policies keep history (like assets) or only the latest (like jobs);
    and a run of each read against a real CommServe, primary and RSC
    account first, since none of these answers has been seen yet.

## Later, decided but not started

23. **Proof jobs: a heartbeat per backup domain** (asked for 2026-11-02).
    A small, known backup that blt sets up in each domain it collects
    from (a CommCell, an RSC account, a NetBackup primary, a Veeam
    server) - a tiny file or folder on a schedule - and expects to see
    succeed within its window. If it does not appear, something in the
    chain is broken: the product, its scheduling, or blt's own
    collection. It proves the whole path end to end, which no single job
    can. To decide: what it backs up and where in each product, how blt
    tells its own proof jobs apart (a naming convention or a dedicated
    plan / SLA / policy), and alerting when one is late. Shown as kind
    `proof` or similar, in the engineering view (see docs/common-jobs.md,
    decision 3). Each domain's setup shown and agreed before anything is
    created on it.

20. **Prefect, what is left.** Prefect runs blt everywhere now (the lab
    cluster, the work desktop). Still to do: failure notifications; and
    retiring deploy/up.sh once a desktop can run deploy/kube/up.sh
    instead. See docs/prefect.md.

11. **A `blt` command-line tool**, one entry point for operating this.
    `deploy/maintenance.sh` (run health, clearing stuck runs) is a start.
    Still wanted:
    - `blt status [<source>]` - last run, watermark, counts by state,
      what is still active;
    - `blt reset <source>` - **throw the collection away and start
      over**, for when it is easier to rebuild than to repair. Deletes
      that source's jobs and runs so the next collection is a full one.
      Needs a matching API endpoint (the CLI should not reach around the
      API into Postgres), a confirmation prompt, and a decision on scope:
      one source at a time, with "everything" as a separate, louder
      option. Open question: delete rows outright, or keep the run
      history and only clear the jobs and the watermark.

13. **An objects table: the assets being protected, and where each was
    seen.** `common.source` (item 12) and `source_object` (item 16) are
    the start. Still open: `job.object_id` - which asset a job protected
    - and identity across sources: the same server is a client in
    Commvault, a VM in VMware and a CI in ServiceNow. One `object` row
    per source plus a table linking the ones judged to be the same asset
    is much easier to get right than one row per real asset, and doesn't
    block it later. Also to decide: what an "object" is for Commvault -
    the client, the subclient, or both as parent and child. See
    [`docs/asset-refinery.md`](docs/asset-refinery.md) - a draft, not
    current work.

16. **Inventory and validation, follow-ons** (the first slice is done:
    see Done). Roughly in order:
    - **More witnesses.** A second source for "what exists" - SQL Server
      itself, ServiceNow - compared against Commvault's rows. This is
      what closes the blind spot (databases Commvault has not yet seen).
    - **Other workloads.** Only SQL Server databases are inventoried
      below instance level. File system subclients, PostgreSQL, VMs.
    - **Client- and instance-level checks.** A client with no successful
      backup in N days; a subclient with no plan; an agent installed
      with no instance.
    - **System databases.** Find a way to confirm `master`/`model`/
      `msdb` backups rather than report them `unverified`.
    - **Cost on a large CommCell.** Inventory makes several calls per
      client and one per subclient. Fine for 14 clients; needs batching
      or paging before thousands.
    - **Excluded on purpose.** A way to record that a database is
      deliberately not backed up, so it stops being a finding.
    - **History of verdicts**, if "since when has this been unprotected"
      matters; today only the current answer is kept.

14. **MSSQL backup validation** - a prototype has worked in the lab since
    2026-10-06 (`lab/50-sql-validate.sh` in yoki-commvault-lab, its
    docs/lab-noise.md stage 3). What remains is making it part of blt:
    for each SQL Server host, compare the databases that exist with the
    databases that were backed up - ten databases on host X should mean
    ten protected.

    Commvault's list is what *its agent discovered*: a database created
    after the last discovery, an unregistered instance or a host with no
    agent is simply absent, and a check built only on Commvault would
    call that host clean. A trustworthy check needs an independent count
    of what exists (`sys.databases`, or ServiceNow) - the first concrete
    case for docs/asset-refinery.md. Also to settle: what counts as
    "backed up" (ever, within N days, a full within N days plus log
    backups?), and how to treat system databases, offline databases and
    deliberate exclusions.

## Needs a live system to settle

17. **First production trial** (planned 2026-10-06; steps in
    docs/production-setup.md). Things it is expected to settle, none of
    which the eval CommServe could: how long a 500-job page and a
    one-day backfill slice take on a large CommServe; which statuses
    come back as `unknown`; whether any job summaries fail to parse (now
    a logged skip, not a failed run); the minimum Commvault role that
    still sees every job and client; how long `--inventory` takes with
    thousands of clients; and whether the token lifetime and 60-day
    renewal window are the same there.

18. **Commvault Cloud (SaaS / Metallic)** - supported from the
    documentation since 2026-10-09 (`CV_DEPLOYMENT=saas`). **Not yet run
    against a tenant.** First steps and everything still unknown are in
    docs/saas-setup.md; the output of `--probe` against a tenant will
    settle most of it. Likely follow-ons: SaaS workload types in the
    inventory (Microsoft 365, cloud databases, VMs); honouring the
    `X-RateLimit-*` headers on a 429; a call budget shared across runs
    rather than per process.

2. **Check the job listing against real jobs** - mostly done 2026-10-05
   and 2026-10-06 (real jobs of each ending, jobs followed through
   Pending, Running, Suspended and Kill Pending, paging over seven
   pages). Still to see against a real CommServe: the reconcile pass,
   and incrementals.

3. **Is the Commvault token lifetime (2 h) configurable** when a token is
   created, and what is the right value for a scheduled collector?

## Collector

5. **Job detail beyond the summary** (`GET /Job/{id}` details, failure
   reasons, per-attempt info) - today only `jobSummary` is collected.

6. **Certificate pinning for a CommServe** with a self-signed
   certificate whose name doesn't match (the eval OVA), instead of
   `CV_VERIFY_TLS=false`. NetBackup primaries already keep their own CA
   certificate; a CommCell could do the same.

7. **Stale `running` rows in `collection_run`.** A collector killed
   outright (power loss, `kill -9`) never reports its run as failed. It
   doesn't affect the watermark, but nothing marks the run abandoned.
   (Prefect's side is handled - item 26 - but blt's own run record is
   not.)

15. **A renewal whose reply is lost.** Token renewal replaces the pair on
    the CommServe as soon as the request arrives. If the network drops
    before the reply gets back, the new tokens are gone and the old ones
    are dead; the only recovery is a new token from Command Center. Rare,
    not preventable from this side - worth an explicit, unmistakable
    error message when a renewal times out mid-flight.

## Done

29. One "Add a source" drop-down (2026-11-10): NetBackup primaries,
    CommCells (a user and password, or a token pair; a CommServe or a
    Commvault Cloud tenant) and Rubrik accounts (the service account's
    file) added and checked from the UI; blt-commvault-check and
    blt-rubrik-check beside NetBackup's; each source's page changes its
    sign-in. An MSP's shared credentials block is still
    blt-prefect-import's.
27. Jobs page (2026-11-09/10): a Columns picker over 18 fields, widths
    dragged or keyed (ui/src/lib/jobColumns.ts), and a job's raw record
    named for its source ("CommCell cv-toaster's own record").
24. Collection timing per source (2026-11-10, blt.timing): sources on
    their product's schedule spread over the first half of its interval;
    every N hours at fixed times, or daily at a time in the source's time
    zone; a quiet window for any collection; each source's next starts on
    the Sources list and its page. A per-source pause is the Collect
    switch.
25. Remote collectors (2026-11-09) and, on 2026-11-10: the per-source and
    token-renewal locks held in Prefect (blt.flows.locks), so two machines
    or pods never run one source at once; `deploy/collector.sh
    keep-tunnel` keeps the desktop's tunnel up as a systemd user service;
    yoki-blt-infra `clusters/location/blt-collector` and
    docs/location-cluster.md (branch F/Location_collector). What is left
    is item 25 above.
22. Bruno, Postman and Insomnia collections (2026-11-10):
    `blt-api-collections` makes docs/api-tools/ from docs/openapi.json,
    with a Common tasks folder and an environment per install; a test
    keeps them current.

1. Renewing an access token after it expired - works (2026-10-05);
   `renewableUntil` did not move, so the 60 days look fixed from the
   token's creation.
4. "As far back as it can" on a large CommCell - the first run takes 24
   hours, `--backfill` the rest in bounded slices (2026-10-06).
8. Read-side endpoints - `/v1/jobs`, `/v1/jobs/summary`, `/v1/storage`
   and the UI on top of them.
9. A systemd timer instead of cron - superseded: Prefect schedules the
   collections, and `deploy/backup.sh` has its own systemd user timer.
10. Backups of `blt-pgdata` - `deploy/backup.sh`, nightly (2026-10-30).
12. A UUID for each source - `common.source` (2026-11-02).
16. Inventory and validation, first slice - `--inventory`,
    `--validate`, `source_object` (2026-10-06); follow-ons are item 16
    above.
19. The Commvault SDK in its own repository, `yoki-py-commvault-sdk`
    (2026-10-10).
21. The API paths from before /v1 removed (2026-10-30, 2026.10.30.3).
26. Stuck runs (2026-11-09): Prefect's leases on disk
    (`blt-prefect-home`), silent runs marked Crashed
    (`blt-crash-silent-runs`), run health and **Clear stuck runs** on
    Schedules, `deploy/maintenance.sh` (docs/admin.md, "Stuck runs").
28. Legacy TLS keys (2026-11-09): a primary may accept 1024-bit keys
    (`legacy_keys`), for the 2017 CA at work; chain, expiry and host name
    still checked. Follow-up for that site: a NetBackup CA migration,
    then turn the setting off.
