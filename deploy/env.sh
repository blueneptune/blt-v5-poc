# Sourced by deploy/up.sh and deploy/kube/up.sh: read config/blt.env,
# check it, and work out every address and port the pod uses - one place,
# so the two ways of starting blt cannot drift apart.
#
# Sets: BIND API_PORT PG_PORT HOST_NETWORK API_LISTEN_HOST API_LISTEN_PORT
# PG_LISTEN_PORT PG_LISTEN_ADDRESSES LOCAL API_IN_POD PREFECT_ENABLED and, when it
# is on, PREFECT_BIND PREFECT_PORT PREFECT_LISTEN_HOST PREFECT_LISTEN_PORT
# PREFECT_LOCAL MAX_PARALLEL.

# Git Bash on Windows rewrites any argument that looks like a Unix path
# into a Windows one before running a command - so podman was being told
# the Prefect UI's API lives at "C:/Program Files/Git/api" instead of
# "/api". Every argument these scripts pass is for podman, which runs on
# Linux, so none should be rewritten. (Elsewhere this does nothing.)
export MSYS_NO_PATHCONV=1

# BLT_ENV_FILE is for the tests; normally it is config/blt.env.
BLT_ENV_FILE="${BLT_ENV_FILE:-config/blt.env}"
if [[ ! -f "$BLT_ENV_FILE" ]]; then
    echo "${BLT_ENV_FILE} not found - copy config/blt.env.example and fill it in." >&2
    exit 1
fi
# Read with any carriage returns removed: a blt.env saved by a Windows
# editor would otherwise put one on the end of the API key and the
# database password, and the API could then never log in to Postgres.
set -a
# shellcheck disable=SC1090
source <(tr -d '\r' < "$BLT_ENV_FILE")
set +a
: "${BLT_API_KEY:?set BLT_API_KEY in config/blt.env}"
: "${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in config/blt.env}"

is_ip() { [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; }

# Published ports are fixed when the pod is created. After changing
# BLT_BIND_ADDRESS or any port, remove the pod first
# (./deploy/down.sh - the data volume is kept) and start it again.
BIND="${BLT_BIND_ADDRESS:-127.0.0.1}"
if ! is_ip "$BIND"; then
    echo "BLT_BIND_ADDRESS in config/blt.env is '${BIND}', which is not an IP address." >&2
    echo "It should be 127.0.0.1 or 0.0.0.0 (or be left out). The network backend" >&2
    echo "(slirp4netns, pasta, ...) goes in BLT_POD_NETWORK, on its own line." >&2
    exit 1
fi
API_PORT="${BLT_API_PORT:-8088}"
PG_PORT="${BLT_PG_PORT:-5433}"

# Two ways to put the pod on the network.
#
# Normally podman publishes the ports: the containers listen on their
# usual ports (8000, 5432, 4200) and podman forwards the published ports
# to them. For rootless podman that forwarding is done by pasta, which
# does not work everywhere - under WSL it has been seen to answer "No
# route to host" for every published port.
#
# BLT_POD_NETWORK=host avoids forwarding altogether: the pod shares the
# host's network, and each service listens on its published port itself,
# on its bind address. Same addresses from the outside, nothing between.
HOST_NETWORK=false
if [[ "${BLT_POD_NETWORK:-}" == "host" ]]; then
    HOST_NETWORK=true
    API_LISTEN_HOST="$BIND" API_LISTEN_PORT="$API_PORT" PG_LISTEN_PORT="$PG_PORT"
    PG_LISTEN_ADDRESSES="$BIND"
    [[ "$BIND" == "0.0.0.0" ]] && PG_LISTEN_ADDRESSES="*"
else
    API_LISTEN_HOST="0.0.0.0" API_LISTEN_PORT=8000 PG_LISTEN_PORT=5432
    PG_LISTEN_ADDRESSES="*"
fi

# Where something on this machine reaches a service bound to $1.
local_address() {
    if [[ "$1" != "0.0.0.0" && "$1" != "127.0.0.1" ]]; then echo "$1"; else echo 127.0.0.1; fi
}
LOCAL="$(local_address "$BIND")"
# Where a container in the pod reaches the API: on the pod's own network
# it listens on 0.0.0.0:8000; on the host's, on the published address.
API_IN_POD="127.0.0.1"
[[ "$HOST_NETWORK" == true ]] && API_IN_POD="$LOCAL"

# Prefect: the server and one worker, in the same pod. Off unless asked.
PREFECT_ENABLED=false
if [[ "${BLT_PREFECT:-false}" == "true" ]]; then
    PREFECT_ENABLED=true
    : "${PREFECT_DB_PASSWORD:?set PREFECT_DB_PASSWORD in config/blt.env (BLT_PREFECT=true)}"
    : "${PREFECT_AUTH:?set PREFECT_AUTH=user:password in config/blt.env (BLT_PREFECT=true)}"
    if [[ "$PREFECT_AUTH" != *:* ]]; then
        echo "PREFECT_AUTH in config/blt.env must be user:password." >&2
        exit 1
    fi
    # The UI may need to be reachable from more machines than the API and
    # the database do, so it has its own bind address.
    PREFECT_BIND="${BLT_PREFECT_BIND_ADDRESS:-$BIND}"
    if ! is_ip "$PREFECT_BIND"; then
        echo "BLT_PREFECT_BIND_ADDRESS in config/blt.env is not an IP address." >&2
        exit 1
    fi
    PREFECT_PORT="${BLT_PREFECT_PORT:-4200}"
    # How many CommCells may collect at the same time.
    MAX_PARALLEL="${BLT_MAX_PARALLEL:-4}"
    if [[ ! "$MAX_PARALLEL" =~ ^[1-9][0-9]*$ ]]; then
        echo "BLT_MAX_PARALLEL in config/blt.env must be a whole number, 1 or more." >&2
        exit 1
    fi
    if [[ "$HOST_NETWORK" == true ]]; then
        PREFECT_LISTEN_HOST="$PREFECT_BIND" PREFECT_LISTEN_PORT="$PREFECT_PORT"
        PREFECT_LOCAL="$(local_address "$PREFECT_BIND")"
    else
        PREFECT_LISTEN_HOST="0.0.0.0" PREFECT_LISTEN_PORT=4200 PREFECT_LOCAL=127.0.0.1
    fi
fi

build_images() {
    podman build -t blt-api -f Containerfile .
    if [[ "$PREFECT_ENABLED" == true ]]; then
        podman build -t blt-prefect-server -f deploy/prefect/server/Containerfile .
        podman build -t blt-prefect-worker -f deploy/prefect/worker/Containerfile .
    fi
}

# Wait for the API (and Prefect, if on) to answer; on failure, show why.
wait_until_up() {
    local api="http://${LOCAL}:${API_PORT}/healthz" prefect="" ok
    [[ "$PREFECT_ENABLED" == true ]] && \
        prefect="http://$(local_address "$PREFECT_BIND"):${PREFECT_PORT}/api/health"
    for _ in $(seq 1 90); do
        ok=true
        curl -fsS -m 3 "$api" 2>/dev/null | grep -q '"ok"' || ok=false
        if [[ -n "$prefect" ]]; then
            curl -fsS -m 3 -u "$PREFECT_AUTH" "$prefect" >/dev/null 2>&1 || ok=false
        fi
        if [[ "$ok" == true ]]; then
            echo "blt pod is up: API on http://${BIND}:${API_PORT}, Postgres on ${BIND}:${PG_PORT}"
            [[ -n "$prefect" ]] && echo "Prefect UI on http://${PREFECT_BIND}:${PREFECT_PORT}"
            return 0
        fi
        sleep 2
    done
    echo "The pod did not become healthy within 3 minutes (${api}${prefect:+, ${prefect}})." >&2
    echo "--- podman ps" >&2
    podman ps -a --filter pod=blt --format '{{.Names}}  {{.Status}}' >&2
    local name
    for name in blt-api blt-postgres blt-prefect-server blt-prefect-worker; do
        if podman container exists "$name"; then
            echo "--- last lines from ${name}" >&2
            podman logs --tail 15 "$name" >&2 2>&1 || true
        fi
    done
    return 1
}
