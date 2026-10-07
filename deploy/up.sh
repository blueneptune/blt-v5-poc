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
API_PORT="${BLT_API_PORT:-8088}"
PG_PORT="${BLT_PG_PORT:-5433}"

# Two ways to put the pod on the network.
#
# Normally podman publishes the two ports: the containers listen on
# their usual ports (8000, 5432) and podman forwards API_PORT and PG_PORT
# to them. For rootless podman that forwarding is done by pasta, which
# does not work everywhere - under WSL it has been seen to answer "No
# route to host" for every published port.
#
# BLT_POD_NETWORK=host avoids forwarding altogether: the pod shares the
# host's network, and the API and Postgres listen on API_PORT and PG_PORT
# themselves, on BLT_BIND_ADDRESS. Same addresses from the outside,
# nothing in between.
if [[ "${BLT_POD_NETWORK:-}" == "host" ]]; then
    POD_ARGS=(--network host)
    API_LISTEN_HOST="$BIND" API_LISTEN_PORT="$API_PORT" PG_LISTEN_PORT="$PG_PORT"
    PG_LISTEN_ADDRESSES="$BIND"
    [[ "$BIND" == "0.0.0.0" ]] && PG_LISTEN_ADDRESSES="*"
else
    POD_ARGS=(-p "${BIND}:${API_PORT}:8000" -p "${BIND}:${PG_PORT}:5432")
    [[ -n "${BLT_POD_NETWORK:-}" ]] && POD_ARGS+=(--network "$BLT_POD_NETWORK")
    API_LISTEN_HOST="0.0.0.0" API_LISTEN_PORT=8000 PG_LISTEN_PORT=5432
    PG_LISTEN_ADDRESSES="*"
fi
if ! podman pod exists blt; then
    podman pod create --name blt "${POD_ARGS[@]}"
fi

podman run -d --replace --pod blt --name blt-postgres \
    -e POSTGRES_USER=blt -e POSTGRES_DB=blt -e POSTGRES_PASSWORD \
    -e "PGPORT=${PG_LISTEN_PORT}" \
    -v blt-pgdata:/var/lib/postgresql/data \
    docker.io/library/postgres:17 \
    postgres -c "listen_addresses=${PG_LISTEN_ADDRESSES}"

# Containers in a pod share localhost, so the API reaches Postgres there.
podman run -d --replace --pod blt --name blt-api \
    -e BLT_API_KEY \
    -e "BLT_LISTEN_HOST=${API_LISTEN_HOST}" -e "BLT_LISTEN_PORT=${API_LISTEN_PORT}" \
    -e "BLT_DATABASE_URL=postgresql+psycopg://blt:${POSTGRES_PASSWORD}@127.0.0.1:${PG_LISTEN_PORT}/blt" \
    localhost/blt-api

# "Started" is not "working": the API container first waits for Postgres
# and applies migrations, and if that fails it exits and the published
# port answers with an empty reply. Wait for a real answer, and show why
# if there isn't one.
LOCAL="127.0.0.1"
[[ "$BIND" != "0.0.0.0" && "$BIND" != "127.0.0.1" ]] && LOCAL="$BIND"
URL="http://${LOCAL}:${API_PORT}/healthz"
for _ in $(seq 1 45); do
    if curl -fsS -m 3 "$URL" 2>/dev/null | grep -q '"ok"'; then
        echo "blt pod is up: API on http://${BIND}:${API_PORT}, Postgres on ${BIND}:${PG_PORT}"
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
