#!/usr/bin/env bash
# Collect job history from one CommCell and load it into the blt API.
#
#   ./collect.sh <commcell> [--full] [--check] [--log-level DEBUG]
#
# <commcell> names config/<commcell>.env. First run for a CommCell collects
# as far back as Commvault will go; later runs collect only what changed
# since the last successful one. Meant to be run by hand and from a timer.
set -euo pipefail

if [[ $# -lt 1 || "$1" == -* ]]; then
    echo "usage: $0 <commcell> [--full] [--check] [--log-level LEVEL]" >&2
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

# One run per CommCell at a time: a scheduled run that fires while the
# previous one is still going exits instead of collecting on top of it.
mkdir -p .locks
exec 9>".locks/${COMMCELL}.lock"
if ! flock -n 9; then
    echo "a collection for ${COMMCELL} is already running, skipping." >&2
    exit 75
fi

exec uv run --extra collector blt-collect --commcell "$COMMCELL" --config-dir "$ROOT/config" "$@"
