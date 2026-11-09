#!/usr/bin/env bash
# A remote collector: a blt worker on another machine - a tools server
# inside a network this one cannot reach - that collects from the sources
# only it can reach, reaching back to this machine's Prefect and API over
# an ssh tunnel this machine opens (docs/remote-collector.md).
#
#   deploy/collector.sh push HOST NAME     copy the worker image to HOST (if it
#                                          changed) and start collector NAME there
#   deploy/collector.sh tunnel HOST        open the tunnel and keep it open (ctrl-c ends it)
#   deploy/collector.sh ssh-config         the same tunnel as ~/.ssh/config lines
#   deploy/collector.sh status HOST NAME   is it running, is the tunnel up, its last log lines
#   deploy/collector.sh stop HOST NAME     remove it, and its settings file, from HOST
#
# HOST is anything ssh takes: user@host, or a Host from ~/.ssh/config.
# NAME is the collector's name, as sources name it in Administration ->
# Sources: lower case letters, digits and dashes.
#
# Settings, in config/blt.env:
#   BLT_COLLECTOR_PREFECT_PORT  the tunnel's Prefect port on HOST (default 14200)
#   BLT_COLLECTOR_API_PORT      the tunnel's blt API port on HOST (default 18088)
#
# The collector's settings - Prefect's password and blt's API key among
# them - go to HOST through ssh's input, never on a command line, into
# ~/.config/blt/collector-NAME.env (600) there. `stop` deletes it.
#
# After every ./deploy/up.sh that built a new worker image, run push again
# so the collector runs the same blt as this machine.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=deploy/env.sh
source deploy/env.sh

# For the tests: stand-ins for ssh and podman.
SSH="${BLT_COLLECTOR_SSH:-ssh}"
PODMAN="${BLT_COLLECTOR_PODMAN:-podman}"

IMAGE=localhost/blt-prefect-worker:latest
REMOTE_PREFECT_PORT="${BLT_COLLECTOR_PREFECT_PORT:-14200}"
REMOTE_API_PORT="${BLT_COLLECTOR_API_PORT:-18088}"

usage() {
    sed -n '7,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
    exit 2
}

need_prefect() {
    if [[ "$PREFECT_ENABLED" != true ]]; then
        echo "A remote collector needs Prefect: set BLT_PREFECT=true in config/blt.env." >&2
        exit 1
    fi
}

check_name() {
    if [[ ! "$1" =~ ^[a-z0-9][a-z0-9-]{0,62}$ || "$1" == default ]]; then
        echo "A collector's name is lower case letters, digits and dashes, and not 'default'." >&2
        exit 1
    fi
}

# Where this machine's Prefect and API answer, for the tunnel's far ends.
local_prefect() { echo "$(local_address "$PREFECT_BIND"):${PREFECT_PORT}"; }
local_api() { echo "${LOCAL}:${API_PORT}"; }

forwards() {
    printf '%s\n' \
        "127.0.0.1:${REMOTE_PREFECT_PORT}:$(local_prefect)" \
        "127.0.0.1:${REMOTE_API_PORT}:$(local_api)"
}

collector_env() {
    # KEY=value lines for podman --env-file: no quoting, one per line.
    printf '%s\n' \
        "BLT_COLLECTOR=$1" \
        "PREFECT_API_URL=http://127.0.0.1:${REMOTE_PREFECT_PORT}/api" \
        "PREFECT_API_AUTH_STRING=${PREFECT_AUTH}" \
        "BLT_API_URL=http://127.0.0.1:${REMOTE_API_PORT}" \
        "BLT_API_KEY=${BLT_API_KEY}" \
        "BLT_MAX_PARALLEL=${MAX_PARALLEL}"
}

push() {
    local host="$1" name="$2" here there
    if ! here="$("$PODMAN" image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null)"; then
        echo "No ${IMAGE} on this machine: run ./deploy/up.sh first (with BLT_PREFECT=true)." >&2
        exit 1
    fi
    there="$("$SSH" "$host" "podman image inspect --format '{{.Id}}' ${IMAGE} 2>/dev/null" || true)"
    if [[ "$here" == "$there" ]]; then
        echo "${host} already has this worker image."
    else
        echo "Copying the worker image to ${host} (a few hundred MB, compressed)..."
        "$PODMAN" save --format docker-archive "$IMAGE" | gzip -1 \
            | "$SSH" "$host" "gunzip | podman load"
    fi
    collector_env "$name" | "$SSH" "$host" \
        "umask 077 && mkdir -p ~/.config/blt && cat > ~/.config/blt/collector-${name}.env"
    "$SSH" "$host" "podman run -d --replace --name blt-collector-${name} --network host \
--restart unless-stopped --env-file ~/.config/blt/collector-${name}.env ${IMAGE}" >/dev/null
    echo "Collector ${name} is running on ${host}."
    echo "It waits for the tunnel: deploy/collector.sh tunnel ${host}"
    echo "Then point sources at it: Administration -> Sources -> Collector: ${name}"
}

tunnel() {
    local host="$1" args=() forward
    while read -r forward; do args+=(-R "$forward"); done < <(forwards)
    echo "Tunnel to ${host}: its 127.0.0.1:${REMOTE_PREFECT_PORT} is this machine's Prefect," \
        "127.0.0.1:${REMOTE_API_PORT} its blt API. Ctrl-C closes it."
    exec "$SSH" -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=3 "${args[@]}" "$host"
}

ssh_config() {
    local forward
    echo "# Add to the Host entry you already use for the tools server, and the"
    echo "# tunnel comes up with every ssh session to it (one at a time: a second"
    echo "# session only warns that the ports are taken)."
    while read -r forward; do
        echo "    RemoteForward ${forward%%:*}:$(cut -d: -f2 <<<"$forward") ${forward#*:*:}"
    done < <(forwards)
    echo "    ServerAliveInterval 30"
}

status() {
    local host="$1" name="$2"
    "$SSH" "$host" "podman ps -a --filter name=^blt-collector-${name}\$ \
--format '{{.Names}}  {{.Status}}  {{.Image}}'"
    echo "--- tunnel, as the collector sees it"
    "$SSH" "$host" "podman exec blt-collector-${name} python -c '
import urllib.request, urllib.error
for what, url in ((\"Prefect\", \"http://127.0.0.1:${REMOTE_PREFECT_PORT}/api/health\"),
                  (\"blt API\", \"http://127.0.0.1:${REMOTE_API_PORT}/healthz\")):
    try:
        urllib.request.urlopen(url, timeout=5)
        print(what, \"up\")
    except urllib.error.HTTPError:
        print(what, \"up\")  # it answered: the tunnel is there
    except Exception as exc:
        print(what, \"DOWN:\", exc)
'" || true
    echo "--- last log lines"
    "$SSH" "$host" "podman logs --tail 20 blt-collector-${name}" 2>&1 || true
}

stop() {
    local host="$1" name="$2"
    "$SSH" "$host" "podman rm -f --ignore blt-collector-${name} >/dev/null; \
rm -f ~/.config/blt/collector-${name}.env"
    echo "Collector ${name} is gone from ${host}, with its settings file."
    echo "Runs for sources that name it are cancelled unstarted until it is back."
}

command="${1:-}"
case "$command" in
    push | status | stop)
        [[ $# -eq 3 ]] || usage
        need_prefect
        check_name "$3"
        "$command" "$2" "$3"
        ;;
    tunnel)
        [[ $# -eq 2 ]] || usage
        need_prefect
        tunnel "$2"
        ;;
    ssh-config)
        need_prefect
        ssh_config
        ;;
    *) usage ;;
esac
