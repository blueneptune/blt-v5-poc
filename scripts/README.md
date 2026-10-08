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
