#!/bin/sh
# Wait for the Prefect server, then make sure everything the flows need
# is registered with it, then run the worker.
#
# Registering on every start keeps the server in step with this image:
# a new image brings its deployments (prefect.yaml) and block type along.
# A schedule paused in the UI stays paused.
#
# BLT_COLLECTOR=<name> makes it a remote collector instead
# (docs/remote-collector.md): it registers nothing - blt's own worker
# does that - and takes only the runs for the sources that name it, from
# its own work queue. Several machines may run the same collector (a
# region with more than one tools server): they share its queue, each
# run going to one of them. It waits for Prefect for as long as it takes, since
# Prefect is at the far end of an ssh tunnel that may not be up yet.
# The same start runs a Windows collector (docs/windows-collector.md).
set -eu

COLLECTOR="${BLT_COLLECTOR:-}"
POOL="${BLT_WORK_POOL:-blt}"
# prefect.yaml and blt.queues read the pool's name from here too.
export BLT_WORK_POOL="$POOL"
# process: flow runs are processes of this worker (podman, a tools server).
# kubernetes: each is a pod of its own (a cluster; docs/kubernetes.md).
TYPE="${BLT_WORKER_TYPE:-process}"
case "$TYPE" in
    process|kubernetes) ;;
    *) echo "BLT_WORKER_TYPE must be process or kubernetes, not '$TYPE'." >&2; exit 1 ;;
esac
if [ -n "$COLLECTOR" ]; then
    # The remote collector's start - wait for Prefect for as long as it
    # takes, make its queue, run the worker on it - is in Python, shared
    # with the Windows service (blt.flows.collector).
    exec python -m blt.flows.collector
fi

python -m blt.flows.collector --wait-only 180

if [ "$TYPE" = kubernetes ]; then
    # The pool's job template, with this worker's image, written each
    # start so the run pods use the image this worker runs.
    TEMPLATE="$(mktemp)"
    python -m blt.flows.kubernetes > "$TEMPLATE"
    prefect --no-prompt work-pool create "$POOL" --type kubernetes \
        --base-job-template "$TEMPLATE" --overwrite
    rm -f "$TEMPLATE"
else
    prefect --no-prompt work-pool inspect "$POOL" >/dev/null 2>&1 \
        || prefect --no-prompt work-pool create "$POOL" --type process
fi
prefect --no-prompt block register -m blt.flows.blocks
prefect --no-prompt block register -m blt.flows.rubrik
prefect --no-prompt block register -m blt.flows.netbackup
# How many CommCells may collect at the same time (flows.SLOTS).
SLOTS="${BLT_MAX_PARALLEL:-4}"
prefect --no-prompt global-concurrency-limit update blt-commcell-runs --limit "$SLOTS" >/dev/null 2>&1 \
    || prefect --no-prompt global-concurrency-limit create blt-commcell-runs --limit "$SLOTS"
prefect --no-prompt deploy --all --prefect-file /app/prefect.yaml
# Runs whose process is gone are marked Crashed instead of RUNNING for
# ever (blt.flows.automations). Not worth refusing to start over.
python -m blt.flows.automations \
    || echo "Could not set up the blt-crash-silent-runs automation; carrying on." >&2
# Only the default work queue: any other is a remote collector's
# (blt.queues), and its runs are for sources only it can reach.
exec prefect worker start --pool "$POOL" --type "$TYPE" --work-queue default
