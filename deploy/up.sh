#!/usr/bin/env bash
# Build the images and (re)start the "blt" pod: Postgres, the API and the
# web UI, and with BLT_PREFECT=true in config/blt.env the Prefect server
# and worker.
# Safe to re-run; job data lives in the blt-pgdata volume and survives it,
# and the Prefect server's slot leases in blt-prefect-home.
#
# deploy/kube/up.sh starts the same pod from a Kubernetes manifest; this
# script stays until that has proven itself.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=deploy/env.sh
source deploy/env.sh
check_ui_sign_in

build_images

if ! podman pod exists blt; then
    if [[ "$HOST_NETWORK" == true ]]; then
        POD_ARGS=(--network host)
    else
        POD_ARGS=(-p "${API_BIND}:${API_PORT}:8000" -p "${BIND}:${PG_PORT}:5432")
        [[ "$PREFECT_ENABLED" == true ]] && POD_ARGS+=(-p "${PREFECT_BIND}:${PREFECT_PORT}:4200")
        [[ "$UI_ENABLED" == true ]] && POD_ARGS+=(-p "${UI_BIND}:${UI_PORT}:3000")
        [[ -n "${BLT_POD_NETWORK:-}" ]] && POD_ARGS+=(--network "$BLT_POD_NETWORK")
    fi
    podman pod create --name blt "${POD_ARGS[@]}"
elif [[ "$PREFECT_ENABLED" == true && "$HOST_NETWORK" != true ]] \
    && ! podman pod inspect blt --format '{{json .InfraConfig.PortBindings}}' | grep -q '"4200/tcp"'; then
    echo "The blt pod was created before Prefect was turned on, so it does not publish the" >&2
    echo "Prefect port. Run ./deploy/down.sh (the data is kept), then this again." >&2
    exit 1
elif [[ "$UI_ENABLED" == true && "$HOST_NETWORK" != true ]] \
    && ! podman pod inspect blt --format '{{json .InfraConfig.PortBindings}}' | grep -q '"3000/tcp"'; then
    echo "The blt pod was created before the web UI was added, so it does not publish the" >&2
    echo "UI's port. Run ./deploy/down.sh (the data is kept), then this again." >&2
    exit 1
fi

podman run -d --replace --pod blt --name blt-postgres \
    -e POSTGRES_USER=blt -e POSTGRES_DB=blt -e POSTGRES_PASSWORD \
    -e "PGPORT=${PG_LISTEN_PORT}" \
    -v blt-pgdata:/var/lib/postgresql/data \
    docker.io/library/postgres:17 \
    postgres -c "listen_addresses=${PG_LISTEN_ADDRESSES}"

# Containers in a pod share localhost, so the API reaches Postgres there.
# BLT_PREFECT_AUTH is Prefect's basic auth, for the Administration pages.
podman run -d --replace --pod blt --name blt-api \
    -e BLT_API_KEY \
    -e "BLT_PREFECT_API_URL=${API_PREFECT_URL}" -e "BLT_PREFECT_AUTH=${PREFECT_AUTH:-}" \
    -e "BLT_LISTEN_HOST=${API_LISTEN_HOST}" -e "BLT_LISTEN_PORT=${API_LISTEN_PORT}" \
    -e "BLT_DATABASE_URL=postgresql+psycopg://blt:${POSTGRES_PASSWORD}@127.0.0.1:${PG_LISTEN_PORT}/blt" \
    localhost/blt-api

if [[ "$PREFECT_ENABLED" == true ]]; then
    # The server gives itself a database (prefect) on the same Postgres,
    # then serves its API - and no UI (PREFECT_SERVER_UI_ENABLED=false in
    # its image): blt's UI is where sources, schedules and runs are managed.
    podman run -d --replace --pod blt --name blt-prefect-server \
        -e "PG_HOST=127.0.0.1" -e "PG_PORT=${PG_LISTEN_PORT}" \
        -e POSTGRES_PASSWORD -e PREFECT_DB_PASSWORD \
        -e "PREFECT_LISTEN_HOST=${PREFECT_LISTEN_HOST}" \
        -e "PREFECT_LISTEN_PORT=${PREFECT_LISTEN_PORT}" \
        -e "PREFECT_SERVER_API_AUTH_STRING=${PREFECT_AUTH}" \
        -e "PREFECT_SERVER_UI_ENABLED=false" \
        -v blt-prefect-home:/var/lib/prefect \
        localhost/blt-prefect-server
    podman run -d --replace --pod blt --name blt-prefect-worker \
        -e "PREFECT_API_URL=http://${PREFECT_LOCAL}:${PREFECT_LISTEN_PORT}/api" \
        -e "PREFECT_API_AUTH_STRING=${PREFECT_AUTH}" \
        -e "BLT_API_URL=http://${API_IN_POD}:${API_LISTEN_PORT}" \
        -e "BLT_MAX_PARALLEL=${MAX_PARALLEL}" \
        -e BLT_API_KEY \
        localhost/blt-prefect-worker
else
    podman rm -f --ignore blt-prefect-server blt-prefect-worker >/dev/null
fi

if [[ "$UI_ENABLED" == true ]]; then
    # It calls the API inside the pod, from its own server side: the key
    # never reaches a browser.
    podman run -d --replace --pod blt --name blt-ui \
        -e "HOST=${UI_LISTEN_HOST}" -e "PORT=${UI_LISTEN_PORT}" -e "ORIGIN=${UI_ORIGIN}" \
        -e "BLT_API_URL=http://${API_IN_POD}:${API_LISTEN_PORT}" \
        -e BLT_API_KEY \
        -e "BLT_UI_SIGN_IN=${UI_SIGN_IN}" -e "BLT_UI_USER=${BLT_UI_USER:-admin}" \
        -e BLT_UI_PASSWORD_HASH -e BLT_UI_SESSION_SECRET \
        localhost/blt-ui
else
    podman rm -f --ignore blt-ui >/dev/null
fi

wait_until_up
