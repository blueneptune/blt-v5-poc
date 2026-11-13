#!/usr/bin/env bash
# Build the Windows collector bundle in a container, so this machine
# needs only podman, and copy it out:
#
#   deploy/windows/build-bundle.sh [OUT]     (OUT: dist/, by default)
#
# It leaves the image blt-collector-windows behind (BLT_BUNDLE_IMAGE
# names another): deploy/up.sh builds the same image and serves the
# bundle from it (docs/windows-collector.md).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:-$ROOT/dist}"
IMAGE="${BLT_BUNDLE_IMAGE:-blt-collector-windows}"

podman build -t "$IMAGE" -f "$ROOT/deploy/windows/Containerfile" "$ROOT"
mkdir -p "$OUT"
# A scratch image has no command: create (never start) a container to copy from.
container="$(podman create "$IMAGE" /none)"
trap 'podman rm -f "$container" >/dev/null' EXIT
podman cp "$container:/downloads/." "$OUT/"
ls -l "$OUT"/blt-collector-windows*
