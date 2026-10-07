#!/usr/bin/env bash
# lab/45-sql-backup.sh [commcell] [--levels Full,Differential,Transaction_Log]
# See lab/cvlab.py and docs/lab-noise.md.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
COMMCELL="cv-toaster"
if [[ $# -gt 0 && "$1" != -* ]]; then COMMCELL="$1"; shift; fi
exec uv run --extra collector python lab/cvlab.py sql-backup --commcell "$COMMCELL" "$@"
