#!/usr/bin/env bash
# Compare one job as Commvault has it now with what blt has stored.
#
#   scripts/job-lookup.sh <commcell> <job-id> [--refresh]
#
# Reads from both; changes nothing unless --refresh is given, which
# stores Commvault's current copy in blt. See scripts/README.md.
set -euo pipefail
if [[ $# -lt 2 ]]; then
    echo "usage: $0 <commcell> <job-id> [--refresh]" >&2
    exit 64
fi
COMMCELL="$1"
JOB="$2"
shift 2
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec uv run --extra collector blt-lookup --commcell "$COMMCELL" --job "$JOB" --config-dir "$ROOT/config" "$@"
