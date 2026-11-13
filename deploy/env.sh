# Sourced by deploy/up.sh and deploy/kube/up.sh: read config/blt.env,
# check it, and work out every address and port the pod uses - one place,
# so the two ways of starting blt cannot drift apart.
#
# Sets: BIND API_BIND API_PORT PG_PORT HOST_NETWORK API_LISTEN_HOST API_LISTEN_PORT
# PG_LISTEN_PORT PG_LISTEN_ADDRESSES LOCAL API_IN_POD PREFECT_ENABLED and, when it
# is on, PREFECT_BIND PREFECT_PORT PREFECT_LISTEN_HOST PREFECT_LISTEN_PORT
# PREFECT_LOCAL MAX_PARALLEL, API_PREFECT_URL (where the API reaches Prefect,
# empty when it is off), and UI_ENABLED with, when it is on, UI_BIND
# UI_PORT UI_ORIGIN UI_LISTEN_HOST UI_LISTEN_PORT UI_SIGN_IN.

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
# It is read as shell, so anything else in it runs too - a command pasted
# into it as a scratch pad would run on every deploy. Only settings,
# comments and blank lines: stop on anything else, naming the line but not
# showing it (it may hold a password).
BAD_LINES="$(tr -d '\r' < "$BLT_ENV_FILE" \
    | grep -nvE '^[[:space:]]*(#.*)?$|^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=' \
    | cut -d: -f1 | paste -sd, -)" || true
if [[ -n "$BAD_LINES" ]]; then
    echo "${BLT_ENV_FILE}: line ${BAD_LINES} is not a setting (NAME=value), a comment or blank." >&2
    echo "It is read as shell, so it would run: remove or comment it out, then run this again." >&2
    exit 1
fi
unset BAD_LINES
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
# The API alone may be opened wider than Postgres: BLT_API_BIND_ADDRESS,
# by default the same as BLT_BIND_ADDRESS.
API_BIND="${BLT_API_BIND_ADDRESS:-$BIND}"
if ! is_ip "$API_BIND"; then
    echo "BLT_API_BIND_ADDRESS in config/blt.env is '${API_BIND}', which is not an IP address." >&2
    exit 1
fi

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
    API_LISTEN_HOST="$API_BIND" API_LISTEN_PORT="$API_PORT" PG_LISTEN_PORT="$PG_PORT"
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
[[ "$HOST_NETWORK" == true ]] && API_IN_POD="$(local_address "$API_BIND")"

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
    # Prefect serves only its API now (no UI): the worker and blt's API
    # reach it inside the pod, the blt-prefect-* tools on this machine. So
    # it stays on 127.0.0.1 unless BLT_PREFECT_BIND_ADDRESS says otherwise.
    PREFECT_BIND="${BLT_PREFECT_BIND_ADDRESS:-127.0.0.1}"
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

# Where the API reaches Prefect, for the Administration pages.
API_PREFECT_URL=""
[[ "$PREFECT_ENABLED" == true ]] && API_PREFECT_URL="http://${PREFECT_LOCAL}:${PREFECT_LISTEN_PORT}/api"

# The web UI (ui/). On unless BLT_UI=false. It needs signing in: the
# account is BLT_UI_USER (default admin) with BLT_UI_PASSWORD_HASH, the
# session cookie signed with BLT_UI_SESSION_SECRET (`npm run password --
# --secret` in ui/ makes both). BLT_UI_SIGN_IN=off opens it to anyone
# who can reach it - then keep it on 127.0.0.1. It has its own bind
# address, which does not follow BLT_BIND_ADDRESS.
UI_ENABLED=true
[[ "${BLT_UI:-true}" == "false" ]] && UI_ENABLED=false
if [[ "$UI_ENABLED" == true ]]; then
    UI_BIND="${BLT_UI_BIND_ADDRESS:-127.0.0.1}"
    if ! is_ip "$UI_BIND"; then
        echo "BLT_UI_BIND_ADDRESS in config/blt.env is not an IP address." >&2
        exit 1
    fi
    UI_PORT="${BLT_UI_PORT:-8090}"
    # The address browsers use for it: its protocol decides whether the
    # session cookie is https-only (without this the UI assumes https). A
    # form posted to the UI by any name of this machine is accepted, one
    # from another site refused (ui/src/lib/server/origin.ts).
    # HTTPS (BLT_UI_TLS=on): the UI serves config/tls/ui-cert.pem and
    # ui-key.pem - a self-signed pair made on the first start if they are
    # not there; put a CA's pair in their place to stop browsers warning.
    # With it on, /prefect/api and /api pass through to Prefect and blt's
    # API, for remote collectors that can reach this machine only on 443
    # (docs/windows-collector.md). Never without HTTPS.
    UI_TLS="${BLT_UI_TLS:-off}"
    if [[ "$UI_TLS" != on && "$UI_TLS" != off ]]; then
        echo "BLT_UI_TLS in config/blt.env must be on or off." >&2
        exit 1
    fi
    UI_SCHEME=http
    [[ "$UI_TLS" == on ]] && UI_SCHEME=https
    UI_ORIGIN="${BLT_UI_ORIGIN:-${UI_SCHEME}://localhost:${UI_PORT}}"
    if [[ "$UI_TLS" == on && "$UI_ORIGIN" != https://* ]]; then
        echo "BLT_UI_TLS=on: BLT_UI_ORIGIN in config/blt.env must start with https:// (it is ${UI_ORIGIN})." >&2
        exit 1
    fi
    UI_SIGN_IN=on
    [[ "${BLT_UI_SIGN_IN:-on}" == "off" ]] && UI_SIGN_IN=off
    if [[ "$HOST_NETWORK" == true ]]; then
        UI_LISTEN_HOST="$UI_BIND" UI_LISTEN_PORT="$UI_PORT"
    else
        UI_LISTEN_HOST="0.0.0.0" UI_LISTEN_PORT=3000
    fi
fi

UI_TLS_DIR="${BLT_UI_TLS_DIR:-${ROOT:-.}/config/tls}"

# The UI's certificate and key, made self-signed if either is missing:
# for every name and address of this machine, and the name in
# BLT_UI_ORIGIN. A pair put there by hand (from a CA) is left alone.
ensure_ui_certificate() {
    local cert="$UI_TLS_DIR/ui-cert.pem" key="$UI_TLS_DIR/ui-key.pem"
    [[ -s "$cert" && -s "$key" ]] && return 0
    command -v openssl >/dev/null || {
        echo "BLT_UI_TLS=on needs openssl to make a certificate, or put ui-cert.pem and ui-key.pem in ${UI_TLS_DIR}." >&2
        return 1
    }
    local names=() name address san="" origin_host
    origin_host="${UI_ORIGIN#https://}"
    origin_host="${origin_host%%[:/]*}"
    for name in "$origin_host" "$(hostname -f 2>/dev/null || true)" "$(hostname -s 2>/dev/null || true)" localhost; do
        [[ -n "$name" ]] && ! is_ip "$name" && names+=("DNS:${name}")
    done
    for address in 127.0.0.1 $(hostname -I 2>/dev/null || true) "$origin_host"; do
        is_ip "$address" && names+=("IP:${address}")
    done
    san="$(printf '%s\n' "${names[@]}" | awk '!seen[$0]++' | paste -sd, -)"
    mkdir -p "$UI_TLS_DIR"
    chmod 700 "$UI_TLS_DIR"
    (umask 077 && openssl req -x509 -newkey rsa:3072 -nodes -days 825 -sha256 \
        -subj "/CN=${origin_host:-localhost}/O=blt" -addext "subjectAltName=${san}" \
        -keyout "$key" -out "$cert" 2>/dev/null)
    chmod 644 "$cert"
    echo "Made a self-signed certificate for the UI (${san}): ${cert}"
}

# The UI's sign-in settings, checked by the start scripts only (backup.sh
# and down.sh source this file too, and need none of it).
check_ui_sign_in() {
    [[ "$UI_ENABLED" == true ]] || return 0
    if [[ "$UI_SIGN_IN" == off ]]; then
        if [[ "$UI_BIND" != "127.0.0.1" ]]; then
            echo "BLT_UI_SIGN_IN=off opens the UI to anyone: keep BLT_UI_BIND_ADDRESS at 127.0.0.1." >&2
            exit 1
        fi
        return 0
    fi
    local how="(cd ui && npm run --silent password -- --secret >> ../config/blt.env)" secret="${BLT_UI_SESSION_SECRET:-}"
    if [[ "${BLT_UI_PASSWORD_HASH:-}" != scrypt:* ]]; then
        echo "Set BLT_UI_PASSWORD_HASH in config/blt.env to a hash from npm run password ${how}." >&2
        exit 1
    fi
    if (( ${#secret} < 32 )); then
        echo "Set BLT_UI_SESSION_SECRET in config/blt.env: 32 characters or more ${how}." >&2
        exit 1
    fi
}

# BLT_SKIP_BUILD=true uses the images already on this machine: on one
# that got them from deploy/move.sh, with no internet to build them.
build_images() {
    if [[ "${BLT_SKIP_BUILD:-}" == true ]]; then
        echo "Using the images already here (BLT_SKIP_BUILD=true)."
        return 0
    fi
    podman build -t blt-api -f Containerfile .
    [[ "$UI_ENABLED" == true ]] && podman build -t blt-ui -f ui/Containerfile ui
    if [[ "$PREFECT_ENABLED" == true ]]; then
        podman build -t blt-prefect-server -f deploy/prefect/server/Containerfile .
        podman build -t blt-prefect-worker -f deploy/prefect/worker/Containerfile .
    fi
}

# Wait for the API (and Prefect, if on) to answer; on failure, show why.
wait_until_up() {
    local api="http://${LOCAL}:${API_PORT}/healthz" prefect="" ui="" ok
    [[ "$PREFECT_ENABLED" == true ]] && \
        prefect="http://$(local_address "$PREFECT_BIND"):${PREFECT_PORT}/api/health"
    [[ "$UI_ENABLED" == true ]] && ui="${UI_SCHEME}://$(local_address "$UI_BIND"):${UI_PORT}/healthz"
    for _ in $(seq 1 90); do
        ok=true
        curl -fsS -m 3 "$api" 2>/dev/null | grep -q '"ok"' || ok=false
        if [[ -n "$prefect" ]]; then
            curl -fsS -m 3 -u "$PREFECT_AUTH" "$prefect" >/dev/null 2>&1 || ok=false
        fi
        if [[ -n "$ui" ]]; then
            # -k: only whether it answers; the certificate may be self-signed.
            curl -kfsS -m 3 "$ui" 2>/dev/null | grep -q '"ok"' || ok=false
        fi
        if [[ "$ok" == true ]]; then
            echo "blt pod is up: API on http://${API_BIND}:${API_PORT}, Postgres on ${BIND}:${PG_PORT}"
            [[ -n "$prefect" ]] && echo "Prefect API on http://${PREFECT_BIND}:${PREFECT_PORT}/api"
            [[ -n "$ui" ]] && echo "blt UI on ${UI_SCHEME}://${UI_BIND}:${UI_PORT}"
            if [[ -n "$ui" && "$UI_TLS" == on ]]; then
                echo "Remote collectors reach blt at ${UI_ORIGIN} (/prefect/api and /api pass through)"
            fi
            return 0
        fi
        sleep 2
    done
    echo "The pod did not become healthy within 3 minutes (${api}${prefect:+, ${prefect}}${ui:+, ${ui}})." >&2
    echo "--- podman ps" >&2
    podman ps -a --filter pod=blt --format '{{.Names}}  {{.Status}}' >&2
    local name
    for name in blt-api blt-postgres blt-prefect-server blt-prefect-worker blt-ui; do
        if podman container exists "$name"; then
            echo "--- last lines from ${name}" >&2
            podman logs --tail 15 "$name" >&2 2>&1 || true
        fi
    done
    return 1
}
