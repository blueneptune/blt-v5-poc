#!/usr/bin/env bash
# lab/30-clients.sh [commcell] [--count N]  -  see lab/cvlab.py and docs/lab-noise.md
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
COMMCELL="cv-toaster"
if [[ $# -gt 0 && "$1" != -* ]]; then COMMCELL="$1"; shift; fi
exec uv run --extra collector python lab/cvlab.py clients --commcell "$COMMCELL" "$@"
