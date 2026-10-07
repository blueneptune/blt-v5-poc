#!/usr/bin/env bash
# Collect job history from one CommCell and load it into the blt API.
#
#   ./collect.sh <commcell> [--backfill] [--full] [--check] [--log-level DEBUG]
#   ./collect.sh <commcell> --inventory            what exists: clients, instances, databases
#   ./collect.sh <commcell> --validate [--max-age-hours N]    is all of it backed up?
#   ./collect.sh <commcell> --report [--history]   per SQL instance: discovered vs backed up
#
# <commcell> names config/<commcell>.env. The first run for a CommCell
# collects what is active plus the last 24 hours; later runs collect only
# what changed since the last successful one. --backfill fetches older
# history a batch of time slices at a time: repeat it until it says it is
# complete. Meant to be run by hand and from a timer.
set -euo pipefail

if [[ $# -lt 1 || "$1" == -* ]]; then
    echo "usage: $0 <commcell> [--backfill] [--full] [--check] [--log-level LEVEL]" >&2
    exit 64
fi
COMMCELL="$1"
shift

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -f "config/${COMMCELL}.env" ]]; then
    echo "config/${COMMCELL}.env not found - copy config/commcell.env.example to it." >&2
    exit 66
fi

# Overlap protection (one run of each kind per CommCell at a time) is
# done by blt-collect itself, so it works the same on every platform and
# when blt-collect is run directly.
exec uv run --extra collector blt-collect --commcell "$COMMCELL" --config-dir "$ROOT/config" "$@"
