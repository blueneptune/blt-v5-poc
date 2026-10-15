#!/usr/bin/env bash
# Start blt from the Kubernetes manifest (deploy/kube/blt.yaml) with
# `podman kube play`: the same pod deploy/up.sh builds, from the same
# config/blt.env. Safe to re-run; the pod is replaced, the data is kept.
#
#   deploy/kube/up.sh            build the images, play the manifest
#   deploy/kube/up.sh --render   print the manifest instead, secrets masked
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
# shellcheck source=deploy/env.sh
source deploy/env.sh

b64() { printf '%s' "$1" | base64 | tr -d '\n'; }

render() {
    local masked="${1:-false}" text fragment="" name value
    text="$(cat deploy/kube/blt.yaml)"
    [[ "$PREFECT_ENABLED" == true ]] && fragment="$(cat deploy/kube/prefect-containers.yaml)"
    text="${text//"# @PREFECT_CONTAINERS@"/$fragment}"

    local -A values=(
        [HOST_NETWORK]="$HOST_NETWORK" [BIND]="$BIND"
        [API_PORT]="$API_PORT" [PG_PORT]="$PG_PORT"
        [API_LISTEN_HOST]="$API_LISTEN_HOST" [API_LISTEN_PORT]="$API_LISTEN_PORT"
        [PG_LISTEN_PORT]="$PG_LISTEN_PORT" [PG_LISTEN_ADDRESSES]="$PG_LISTEN_ADDRESSES"
        [API_IN_POD]="$API_IN_POD"
        [PREFECT_BIND]="${PREFECT_BIND:-}" [PREFECT_PORT]="${PREFECT_PORT:-}"
        [PREFECT_LISTEN_HOST]="${PREFECT_LISTEN_HOST:-}"
        [PREFECT_LISTEN_PORT]="${PREFECT_LISTEN_PORT:-}" [PREFECT_LOCAL]="${PREFECT_LOCAL:-}"
    )
    local -A secrets=(
        [BLT_API_KEY]="$BLT_API_KEY" [POSTGRES_PASSWORD]="$POSTGRES_PASSWORD"
        [BLT_DATABASE_URL]="postgresql+psycopg://blt:${POSTGRES_PASSWORD}@127.0.0.1:${PG_LISTEN_PORT}/blt"
        [PREFECT_DB_PASSWORD]="${PREFECT_DB_PASSWORD:-unused}"
        [PREFECT_AUTH]="${PREFECT_AUTH:-unused:unused}"
    )
    for name in "${!values[@]}"; do
        text="${text//"\${${name}}"/${values[$name]}}"
    done
    for name in "${!secrets[@]}"; do
        value="$(b64 "${secrets[$name]}")"
        [[ "$masked" == true ]] && value="********"
        text="${text//"\${B64_${name}}"/$value}"
    done
    if [[ "$text" == *'${'* ]]; then
        echo "deploy/kube/blt.yaml has a value this script does not fill in:" >&2
        grep -o '\${[A-Z0-9_]*}' <<<"$text" | sort -u >&2
        exit 1
    fi
    printf '%s\n' "$text"
}

if [[ "${1:-}" == "--render" ]]; then
    render true
    exit 0
fi

build_images
# From stdin: the rendered manifest, secrets and all, never touches disk.
render | podman kube play --replace -
wait_until_up
