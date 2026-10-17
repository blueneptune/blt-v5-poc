#!/bin/sh
# Wait for the Prefect server, then make sure everything the flows need
# is registered with it, then run the worker.
#
# Registering on every start keeps the server in step with this image:
# a new image brings its deployments (prefect.yaml) and block type along.
# A schedule paused in the UI stays paused.
set -eu

echo "Waiting for the Prefect server at ${PREFECT_API_URL}"
python - <<'PY'
import sys, time
from prefect.client.orchestration import get_client

deadline = time.monotonic() + 180
while True:
    try:
        with get_client(sync_client=True) as client:
            if client.api_healthcheck() is None:
                break
    except Exception:
        pass
    if time.monotonic() > deadline:
        sys.exit("The Prefect server did not answer within 180 s.")
    time.sleep(3)
PY

POOL="${BLT_WORK_POOL:-blt}"
prefect --no-prompt work-pool inspect "$POOL" >/dev/null 2>&1 \
    || prefect --no-prompt work-pool create "$POOL" --type process
prefect --no-prompt block register -m blt.flows.blocks
# How many CommCells may collect at the same time (flows.SLOTS).
SLOTS="${BLT_MAX_PARALLEL:-4}"
prefect --no-prompt global-concurrency-limit update blt-commcell-runs --limit "$SLOTS" >/dev/null 2>&1 \
    || prefect --no-prompt global-concurrency-limit create blt-commcell-runs --limit "$SLOTS"
prefect --no-prompt deploy --all --prefect-file /app/prefect.yaml
exec prefect worker start --pool "$POOL" --type process
