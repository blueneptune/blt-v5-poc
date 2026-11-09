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
set -eu

COLLECTOR="${BLT_COLLECTOR:-}"
POOL="${BLT_WORK_POOL:-blt}"
if [ -n "$COLLECTOR" ]; then
    case "$COLLECTOR" in
        default|-*|*[!a-z0-9-]*)
            echo "BLT_COLLECTOR must be lower case letters, digits and dashes, and not 'default'." >&2
            exit 1 ;;
    esac
    WAIT_SECONDS=0
else
    WAIT_SECONDS=180
fi

echo "Waiting for the Prefect server at ${PREFECT_API_URL}"
BLT_WAIT_SECONDS="$WAIT_SECONDS" python - <<'PY'
import os, sys, time
from prefect.client.orchestration import get_client

limit = int(os.environ["BLT_WAIT_SECONDS"])  # 0: for as long as it takes
started = said = time.monotonic()
while True:
    try:
        with get_client(sync_client=True) as client:
            if client.api_healthcheck() is None:
                break
    except Exception:
        pass
    if limit and time.monotonic() - started > limit:
        sys.exit(f"The Prefect server did not answer within {limit} s.")
    if time.monotonic() - said > 300:
        print("Still waiting for the Prefect server - is the ssh tunnel up?", flush=True)
        said = time.monotonic()
    time.sleep(3)
PY

if [ -n "$COLLECTOR" ]; then
    echo "Remote collector ${COLLECTOR}: taking work queue ${COLLECTOR} of pool ${POOL}"
    # A worker does not make its queue, and asking a missing one for
    # work is refused: make it. The pool is blt's own worker's to make.
    prefect --no-prompt work-queue inspect "$COLLECTOR" --pool "$POOL" >/dev/null 2>&1 \
        || prefect --no-prompt work-queue create "$COLLECTOR" --pool "$POOL"
    exec prefect worker start --pool "$POOL" --type process --work-queue "$COLLECTOR" \
        --name "blt-collector-${COLLECTOR}-$(hostname)"
fi

prefect --no-prompt work-pool inspect "$POOL" >/dev/null 2>&1 \
    || prefect --no-prompt work-pool create "$POOL" --type process
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
exec prefect worker start --pool "$POOL" --type process --work-queue default
