# TODO

Open items, roughly in the order they matter, grouped by area. Numbers
never change, so a number in a commit or a conversation still finds its
item; finished ones move to [Done](#done) at the end, one line each.

## Next up (asked for 2026-11-09 and 2026-11-10)

29. **One "Add a source" button** (asked for 2026-11-10). The Sources
    page's "Add a NetBackup primary" becomes "Add a source", with a
    choice of product, each opening its own form:
    - **NetBackup primary** - today's form (address, fetch its CA, API
      key, legacy key sizes);
    - **CommCell** (Commvault on-prem, and Commvault Cloud) - address,
      CA, user name and password or token, as blt-prefect-import takes
      them today;
    - **Rubrik account** - upload the service account's JSON file, as
      blt-rubrik takes it today; the secret goes to the source's block
      and is never shown again.
    Each saves through the admin API (new POST endpoints beside the
    NetBackup one) and runs Check. The CLI imports stay, and the note
    "CommCells and Rubrik accounts get their credentials from
    blt-prefect-import and blt-rubrik for now" goes.

27. **Jobs page** (asked for 2026-11-09). Columns and widths are done
    (see Done). Still to do: in a job's detail, "the product's own
    record" named for the source it came from: the CommCell, the primary
    server, the Rubrik account.

24. **Per-source collection timing** (asked for 2026-11-09). Today each
    kind of collection has one schedule in `prefect.yaml` and every
    source with Collect on runs at the same moment. With 20-30 sources
    that bunches the load, so:
    - a **Collection schedule** on each source, set on its edit page:
      "default" (the product's schedule), every N hours, or daily at a
      time; plus an optional quiet window ("not between 22:00 and
      04:00") for a domain's busy backup hours;
    - an **automatic spread** for sources left on "default": each gets a
      stable offset inside the interval (from its UUID), so 30 primaries
      on "hourly" don't all start at :27;
    - the parent flow still fires on the finest interval and skips any
      source not due, so there is still one schedule per product to
      pause; the product's interval is the finest a source can get;
    - Administration shows each source's next run, and a per-source
      pause next to the Collect switch.

25. **Remote collectors, what is left** (the collectors themselves are
    done: see Done, and docs/remote-collector.md). The direction
    (2026-11-10): a small Kubernetes cluster in each location runs a
    Prefect worker for that location's work pool, and each source's run
    is its own pod. Toward that:
    - a per-source lock held in Prefect, not in a file on one machine,
      so two machines - or two pods - never run the same source at once,
      and never renew one CommCell's tokens at once;
    - a location cluster's worker: a Kubernetes work pool and its
      manifests, straight to core's ingresses (no tunnel);
    - keeping the desktop's tunnel up without a terminal (a systemd user
      unit in WSL);
    - an API key that may only collect, for collectors (with Zitadel).

30. **Policies, read only** (asked for 2026-11-10). For each backup
    product, a read view of its storage policies and its backup
    policies: Commvault plans and storage policies, NetBackup policies
    and storage lifecycle policies, Rubrik SLA domains. Collected like
    storage, shown on their own page, nothing changed in the product.

22. **Ready-made imports for API tools** (asked for 2026-10-30):
    collections for Bruno, Postman and Insomnia generated from
    `docs/openapi.json`, an environment per install (lab, work, a
    customer's) with the key left blank, and example requests for the
    common tasks. Generated like the spec, so they cannot drift.

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
25. Remote collectors (2026-11-09): each source names its collector,
    runs go to its work queue, `deploy/collector.sh` sets one up over
    ssh (docs/remote-collector.md). What is left is item 25 above.
26. Stuck runs (2026-11-09): Prefect's leases on disk
    (`blt-prefect-home`), silent runs marked Crashed
    (`blt-crash-silent-runs`), run health and **Clear stuck runs** on
    Schedules, `deploy/maintenance.sh` (docs/admin.md, "Stuck runs").
27. Jobs page columns (2026-11-09): a Columns picker over 18 fields, and
    widths dragged or keyed, kept per browser (ui/src/lib/jobColumns.ts).
    What is left is item 27 above.
28. Legacy TLS keys (2026-11-09): a primary may accept 1024-bit keys
    (`legacy_keys`), for the 2017 CA at work; chain, expiry and host name
    still checked. Follow-up for that site: a NetBackup CA migration,
    then turn the setting off.
