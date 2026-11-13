# scripts

Small commands for looking into what blt has collected. They read; they
do not change a CommServe.

## job-lookup.sh - is blt wrong about a job, or just behind?

```
scripts/job-lookup.sh <commcell> <job-id>
scripts/job-lookup.sh <commcell> <job-id> --refresh
```

Asks the CommServe for the job as it is right now, asks blt for the row
it has stored, and prints the two side by side. A `*` marks each field
that differs, and the last line says what the difference means: in
step, behind (and by how long), not collected yet, or gone from the
CommServe.

`--refresh` also stores the CommServe's current copy in blt, the same
way a collection run would. That is the only thing here that writes, and
it writes to blt's database, not to Commvault.

On Windows without Git Bash, run what the script runs:

```
uv run --extra collector blt-lookup --commcell <commcell> --job <job-id>
```

### Reading the result

- **Still active, and behind by less than your collection interval** -
  normal. An active job's numbers move between collections.
- **Behind by more than your collection interval** - collection is not
  running or not succeeding. Look at the latest rows of `collection_run`.
- **Finished, and different** - blt collected the job before it ended
  and has not been back. The next collection fixes it.
- **Commvault's own numbers are not what you expect** - then it is not a
  blt problem. In particular `elapsed seconds` is Commvault's figure,
  and it is *not* wall-clock time: it stops while a job is suspended or
  waiting. A job that started 73 seconds before it ended has been seen
  to report 23. For "how long has this been going", use the start time.

## nbu-flows-check.sh - can blt draw NetBackup's Sankey?

```
scripts/nbu-flows-check.sh        # the last 7 days
scripts/nbu-flows-check.sh 30
```

Read-only, on the machine blt's pod runs on. Four tables:

1. **Jobs per primary and type**: backups, duplications, replications
   and imports that ended in the window. Replications on one primary
   and imports on the other are what the Sankey joins.
2. **Replications, matched or not**: each to the primary whose import
   of the same image (same backup id) blt has. `(not matched)` rows are
   drawn to the storage unit the job names, and counted under the chart.
3. **Storage units in no collected disk pool**: jobs that wrote there
   land on a node of that name instead of a pool (tape, or a pool whose
   storage is not collected).
4. **A few replication jobs as NetBackup gave them**, to see what it
   names as their destination.
