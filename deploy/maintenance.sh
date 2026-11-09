#!/usr/bin/env bash
# Stuck runs from a terminal - Administration -> Schedules' "Run health"
# and "Clear stuck runs", for when the web UI is not there
# (docs/admin.md, "Stuck runs").
#
#   deploy/maintenance.sh status      are runs getting through? (exit 1 if not)
#   deploy/maintenance.sh unstick     cancel stuck runs, give back leaked slots
#   deploy/maintenance.sh unstick --dry-run    only say what it would do
#
# Either takes --stuck-after-minutes N: how long a run may run before it
# counts as stuck (default 120).
#
# It runs inside the blt-api container (blt.api.maintenance), which has
# the API key: the key is never on a command line. On the cluster:
#   kubectl -n blt exec deploy/blt-api -- python -m blt.api.maintenance status
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=deploy/env.sh
source deploy/env.sh

PODMAN="${BLT_MAINTENANCE_PODMAN:-podman}"  # for the tests

case "${1:-}" in
    status | unstick) ;;
    *)
        sed -n '6,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
        exit 2
        ;;
esac
if [[ "$PREFECT_ENABLED" != true ]]; then
    echo "Runs are Prefect's: set BLT_PREFECT=true in config/blt.env." >&2
    exit 1
fi
if [[ "$("$PODMAN" container inspect -f '{{.State.Running}}' blt-api 2>/dev/null)" != true ]]; then
    echo "blt-api is not running - start blt with ./deploy/up.sh first." >&2
    exit 1
fi
exec "$PODMAN" exec blt-api python -m blt.api.maintenance "$@"
