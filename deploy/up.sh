#!/usr/bin/env bash
# Build the API image and (re)start the "blt" pod: Postgres + the API.
# Safe to re-run; job data lives in the blt-pgdata volume and survives it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f config/blt.env ]]; then
    echo "config/blt.env not found - copy config/blt.env.example and fill it in." >&2
    exit 1
fi
# Read with any carriage returns removed: a blt.env saved by a Windows
# editor would otherwise put one on the end of the API key and the
# database password, and the API could then never log in to Postgres.
set -a
# shellcheck disable=SC1090
source <(tr -d '\r' < config/blt.env)
set +a
: "${BLT_API_KEY:?set BLT_API_KEY in config/blt.env}"
: "${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in config/blt.env}"

podman build -t blt-api -f Containerfile .

# Published ports are fixed when the pod is created. After changing
# BLT_BIND_ADDRESS or either port, remove the pod first
# (./deploy/down.sh - the data volume is kept) and run this again.
BIND="${BLT_BIND_ADDRESS:-127.0.0.1}"
if [[ ! "$BIND" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "BLT_BIND_ADDRESS in config/blt.env is '${BIND}', which is not an IP address." >&2
    echo "It should be 127.0.0.1 or 0.0.0.0 (or be left out). The network backend" >&2
    echo "(slirp4netns, pasta, ...) goes in BLT_POD_NETWORK, on its own line." >&2
    exit 1
fi
# BLT_POD_NETWORK picks how rootless podman connects the pod to the host
# (e.g. slirp4netns). Unset means podman's default, which is pasta on
# podman 5 - and pasta is known to misbehave under WSL, where published
# ports can answer "No route to host" or with an empty reply.
NETWORK_ARGS=()
[[ -n "${BLT_POD_NETWORK:-}" ]] && NETWORK_ARGS=(--network "$BLT_POD_NETWORK")
if ! podman pod exists blt; then
    podman pod create --name blt "${NETWORK_ARGS[@]}" \
        -p "${BIND}:${BLT_API_PORT:-8088}:8000" \
        -p "${BIND}:${BLT_PG_PORT:-5433}:5432"
fi

podman run -d --replace --pod blt --name blt-postgres \
    -e POSTGRES_USER=blt -e POSTGRES_DB=blt -e POSTGRES_PASSWORD \
    -v blt-pgdata:/var/lib/postgresql/data \
    docker.io/library/postgres:17

# Containers in a pod share localhost, so the API reaches Postgres there.
podman run -d --replace --pod blt --name blt-api \
    -e BLT_API_KEY \
    -e "BLT_DATABASE_URL=postgresql+psycopg://blt:${POSTGRES_PASSWORD}@127.0.0.1:5432/blt" \
    localhost/blt-api

# "Started" is not "working": the API container first waits for Postgres
# and applies migrations, and if that fails it exits and the published
# port answers with an empty reply. Wait for a real answer, and show why
# if there isn't one.
LOCAL="127.0.0.1"
[[ "$BIND" != "0.0.0.0" && "$BIND" != "127.0.0.1" ]] && LOCAL="$BIND"
URL="http://${LOCAL}:${BLT_API_PORT:-8088}/healthz"
for _ in $(seq 1 45); do
    if curl -fsS -m 3 "$URL" 2>/dev/null | grep -q '"ok"'; then
        echo "blt pod is up: API on http://${BIND}:${BLT_API_PORT:-8088}, Postgres on ${BIND}:${BLT_PG_PORT:-5433}"
        exit 0
    fi
    sleep 2
done

echo "The API did not become healthy at ${URL} within 90 seconds." >&2
echo "--- podman ps" >&2
podman ps -a --filter pod=blt --format '{{.Names}}  {{.Status}}' >&2
echo "--- last lines from blt-api" >&2
podman logs --tail 25 blt-api >&2 2>&1 || true
echo "--- last lines from blt-postgres" >&2
podman logs --tail 10 blt-postgres >&2 2>&1 || true
exit 1
