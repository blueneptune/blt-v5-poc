# Asset refinery (draft)

**Status: a first draft of a direction, written 2026-10-06. Nothing here
is built, and nothing here is scheduled.** The current work is running
the Commvault collection as it stands to get real-world data. This
document is a starting point to be rewritten from that experience - where
it and the owner's own write-up disagree, the write-up wins.

## The idea

blt today collects backup jobs from Commvault. The larger aim is a
collection tool for a whole estate that can link what different systems
say about the same things.

The motivating case, which really happens:

- an asset is recorded in **ServiceNow**, the source of truth for what
  should exist;
- it is backed up as a **VM** by a NetBackup domain;
- the **MSSQL databases** on that same machine are protected by a
  different tool in a different domain - another NetBackup, or Commvault.

Three systems, three names, one machine. The refinery acquires metadata
from all of them and maps it together, so the answer to "what is this
asset, and how is it protected?" is as accurate as the evidence allows.

## Three layers

Kept strictly separate, because each changes for a different reason.

| Layer | Holds | Changes when |
|---|---|---|
| **Observation** | What one source said about one thing, as it said it | That source is collected again |
| **Asset** | The refined record: one per real thing | The mapping decides so |
| **Link** | Which observations belong to which asset, and why | Rules change, or a person decides |

Observations are never merged or overwritten by the refined view. That
is what makes it possible to change the matching rules and re-run them
over data already collected, and to answer "why does it think these two
are the same machine?" from the evidence stored on the link.

## Assets, workloads, protection

The VM and the databases in the example are not two views of one object.
They are two workloads on the same asset, protected separately.

```
asset          sql-prod-07 (the server)
 ├─ workload   VM image             <- protected by NetBackup, domain X
 ├─ workload   MSSQL instance
 │   └─ workload   database Sales   <- protected by Commvault, domain Y
 └─ workload   file system          <- protected by nothing
```

- **Asset**: the thing ServiceNow would have a CI for.
- **Workload**: something on or of an asset that can be protected on its
  own - a VM image, a database instance, a database, a file system.
- **Protection**: evidence that a workload is backed up - by which tool,
  in which domain, under what policy, and the jobs that prove it.

"Is this asset protected?" is then a coverage question across its
workloads, and the gaps are visible rather than hidden behind a single
yes: imaged nightly, but the databases on it backed up by nothing.

## Sources and what each is the authority on

| Source | Authority on | Not on |
|---|---|---|
| ServiceNow | Whether an asset should exist, who owns it, its lifecycle state | Whether it is protected |
| Backup tools (Commvault, NetBackup, ...) | What is protected, how, and whether the jobs succeed | Whether the asset should exist |
| VMware, cloud inventories | What is actually running, and its hardware-level identifiers | Ownership |

When sources disagree, the disagreement is a finding to surface, not
noise to reconcile away:

- in ServiceNow, protected nowhere;
- protected, unknown to ServiceNow;
- running, in neither;
- one asset recorded twice;
- retired in ServiceNow, still being backed up.

## Matching ("the secret sauce")

To be designed from real data. Principles to start from:

1. **Rank identifiers by strength.**
   - Strong: VMware instance UUID, BIOS UUID, serial number, ServiceNow
     `sys_id`, cloud instance id.
   - Medium: fully qualified name, MAC address.
   - Weak: short host name, IP address.
2. **Deterministic rules before scoring.** A strong identifier in common
   is a match. Scored combinations of weaker ones come second.
3. **Normalise before comparing.** Case, domain suffixes, short versus
   fully qualified names.
4. **Every link carries its evidence**: the rule, the fields, the values,
   a confidence, when it was made.
5. **A person's decision is pinned.** "These are the same" and "these
   are not" both survive every re-run.
6. **Unsure stays unsure.** Candidate matches go to a review queue. A
   confident wrong merge is worse than an open question.

## Known hard cases

- **Names reused or changed over time** - matching needs validity in
  time, not only a current state.
- **Cloned VMs** sharing a BIOS UUID.
- **Clusters and availability groups** - the database belongs to a
  virtual name, not to one server.
- **The same tool twice** - two NetBackup domains, or two CommCells,
  both knowing a client by the same name.
- **Decommissioned assets** whose backups are deliberately retained.

## What carries over from blt as it is

- The split between short-lived collectors and an API that owns the
  data.
- Upserts keyed on the source's own identifier, so collection is
  repeatable.
- Keeping the source's raw record next to the typed columns.
- Watermarks and run records per source.
- The collector pattern itself: one per kind of source, on
  `sdk-primer-core`.

## What would have to change

- **`commcell` becomes a generic `source`** with a kind (`commvault`,
  `netbackup`, `servicenow`, `vmware`, ...) and a UUID key (TODO item
  12). API paths and column names lose their Commvault-specific wording.
- **Jobs point at a workload observation** instead of carrying a client
  name as text (TODO item 13).
- **New tables** for observations, assets, workloads and links.
- **Collection is no longer only "jobs since the watermark".** Inventory
  sources (ServiceNow, VMware) are collected as full or changed-since
  snapshots of what exists, which is a different shape of run.

## Orchestration

Expected to move to **Prefect** at some point: once there are several
kinds of collector, dependencies between them (refine only after the
sources it needs have been collected), retries and visibility across the
lot, a shell script on a timer stops being enough. Not designed here.
`collect.sh` and the per-source lock are the stand-in until then.

## Open questions

- What is an "object" for Commvault: the client, the subclient, or both
  as parent and child? The same question for NetBackup's client and
  policy.
- One observation row per source with a link table, or one row per real
  asset with source records hanging off it? (Leaning to the first: it is
  easier to get right and doesn't prevent the second.)
- Where does the mapping run - inside the API, or as its own stage after
  collection?
- How much history: only the current mapping, or every mapping there has
  been?
- Where do review decisions get made - a UI, a CLI (TODO item 11), a
  file?
- Which second source proves the matching soonest? ServiceNow is the
  most telling pairing with a backup tool: "should exist" against "is
  protected".

## Not now

Deliberately out of scope until the Commvault collection has run against
real environments: every table above, any matching code, any second
collector, Prefect.
