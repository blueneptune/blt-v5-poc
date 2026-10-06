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
set -a
# shellcheck disable=SC1091
source config/blt.env
set +a
: "${BLT_API_KEY:?set BLT_API_KEY in config/blt.env}"
: "${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in config/blt.env}"

podman build -t blt-api -f Containerfile .

if ! podman pod exists blt; then
    podman pod create --name blt \
        -p "127.0.0.1:${BLT_API_PORT:-8088}:8000" \
        -p "127.0.0.1:${BLT_PG_PORT:-5433}:5432"
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

echo "blt pod is up: API on http://127.0.0.1:${BLT_API_PORT:-8088}, Postgres on 127.0.0.1:${BLT_PG_PORT:-5433}"
