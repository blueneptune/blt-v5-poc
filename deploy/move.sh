#!/usr/bin/env bash
# Move blt from this machine to another one - a tools server - with its
# data: the code at this checkout, config/blt.env, the images, and both
# databases (blt, and prefect with its sources, schedules and secrets).
#
#   deploy/move.sh HOST [options]
#
#   --dir DIR             where blt goes on HOST (default: blt, under the home folder)
#   --ui-port PORT        the UI's port there (default 443)
#   --ui-address IP       the address it listens on (default 0.0.0.0: every one)
#   --ui-origin URL       the address people type (default http://<HOST's name>:PORT)
#   --adopt-collector NAME
#                         a remote collector already on HOST (deploy/collector.sh):
#                         point it at the blt now beside it, not at the tunnel
#   --upgrade             later releases: HOST has blt already. Send this
#                         checkout's code and images, back blt up there, restart
#                         it (and its collectors) on them. Settings and data stay.
#
# HOST is anything ssh takes: user@host, or a Host from ~/.ssh/config. It
# needs podman and nothing else - no git, no internet: the code goes as an
# archive of this checkout and the images go over ssh.
#
# Without --upgrade it stops before touching anything if HOST already has
# blt's data (the blt-pgdata volume); with it, if HOST does not.
#
# This machine's blt keeps running throughout. When the move has worked,
# stop it here with ./deploy/down.sh (its data is kept), so the two do not
# both collect. Nothing is lost between the dump and the stop: the next
# run on HOST collects everything since the last one in the dump.
#
# docs/moving.md has the steps around it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=deploy/env.sh
source deploy/env.sh

# For the tests: stand-ins for ssh and podman.
SSH="${BLT_MOVE_SSH:-ssh}"
PODMAN="${BLT_MOVE_PODMAN:-podman}"

IMAGES=(localhost/blt-api localhost/blt-prefect-server localhost/blt-prefect-worker
    localhost/blt-ui docker.io/library/postgres:17)
COLLECTOR_IMAGE=localhost/blt-prefect-worker:latest

usage() {
    sed -n '6,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
    exit 2
}

[[ $# -ge 1 && "$1" != -* ]] || usage
HOST="$1"
shift
DIR=blt UI_PORT_THERE=443 UI_ADDRESS_THERE=0.0.0.0 UI_ORIGIN_THERE="" ADOPT="" UPGRADE=false
while [[ $# -gt 0 ]]; do
    if [[ "$1" == --upgrade ]]; then
        UPGRADE=true
        shift
        continue
    fi
    [[ $# -ge 2 ]] || usage
    case "$1" in
        --dir) DIR="$2" ;;
        --ui-port) UI_PORT_THERE="$2" ;;
        --ui-address) UI_ADDRESS_THERE="$2" ;;
        --ui-origin) UI_ORIGIN_THERE="$2" ;;
        --adopt-collector) ADOPT="$2" ;;
        *) usage ;;
    esac
    shift 2
done

if [[ ! "$DIR" =~ ^[A-Za-z0-9_./-]+$ ]]; then
    echo "--dir: letters, digits and . _ / - only." >&2
    exit 1
fi
if [[ ! "$UI_PORT_THERE" =~ ^[1-9][0-9]{0,4}$ ]] || (( UI_PORT_THERE > 65535 )); then
    echo "--ui-port is not a port number." >&2
    exit 1
fi
if ! is_ip "$UI_ADDRESS_THERE"; then
    echo "--ui-address is not an IP address." >&2
    exit 1
fi
if [[ -n "$ADOPT" && ! "$ADOPT" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]]; then
    echo "--adopt-collector: a collector's name is lower case letters, digits and dashes." >&2
    exit 1
fi
if [[ "$PREFECT_ENABLED" != true || "$UI_ENABLED" != true ]]; then
    echo "This moves blt with Prefect and the UI: BLT_PREFECT=true and the UI on in config/blt.env." >&2
    exit 1
fi
if [[ "$("$PODMAN" container inspect -f '{{.State.Running}}' blt-postgres 2>/dev/null)" != true ]]; then
    echo "blt-postgres is not running here - start blt with ./deploy/up.sh first." >&2
    exit 1
fi

remote() { "$SSH" "$HOST" "$@"; }

# --- 1. Can HOST take it? Nothing is changed there until all of this passes.
echo "Checking ${HOST}..."
if ! remote "command -v podman >/dev/null"; then
    echo "${HOST} has no podman (or ssh to it failed)." >&2
    exit 1
fi
if [[ "$UPGRADE" == true ]]; then
    if ! remote "podman volume exists blt-pgdata && test -f '${DIR}/config/blt.env'"; then
        echo "${HOST} has no blt in ${DIR} to upgrade: move it there first (without --upgrade)." >&2
        exit 1
    fi
elif remote "podman volume exists blt-pgdata"; then
    echo "${HOST} already has blt's data (the blt-pgdata volume): not moving over it." >&2
    echo "This is for the first move. To upgrade blt there: deploy/move.sh ${HOST} --upgrade" >&2
    exit 1
fi
if [[ "$UPGRADE" == false ]] && (( UI_PORT_THERE < 1024 )); then
    lowest="$(remote "sysctl -n net.ipv4.ip_unprivileged_port_start" 2>/dev/null || echo 1024)"
    if (( UI_PORT_THERE < lowest )); then
        echo "Rootless podman on ${HOST} may not listen on port ${UI_PORT_THERE} (below ${lowest})." >&2
        echo "Someone with root there allows it, once:" >&2
        echo "    echo 'net.ipv4.ip_unprivileged_port_start=${UI_PORT_THERE}' | sudo tee /etc/sysctl.d/90-blt.conf" >&2
        echo "    sudo sysctl --system" >&2
        echo "or pick a port of 1024 or more with --ui-port." >&2
        exit 1
    fi
fi
if [[ -n "$ADOPT" ]] && ! remote "podman container exists blt-collector-${ADOPT}"; then
    echo "${HOST} has no collector ${ADOPT} (blt-collector-${ADOPT}) to adopt." >&2
    exit 1
fi
if [[ "$UPGRADE" == false && -z "$UI_ORIGIN_THERE" ]]; then
    UI_ORIGIN_THERE="http://$(remote "hostname -f 2>/dev/null || hostname"):${UI_PORT_THERE}"
fi
VERSION="$(git describe --tags --always --dirty)"
if [[ "$VERSION" == *-dirty ]]; then
    echo "This checkout has changes not committed; ${HOST} would get a blt no tag describes." >&2
    echo "Commit or stash them (or check out a release tag) first." >&2
    exit 1
fi

# --- 2. The code, as committed here, and the settings.
echo "Copying blt ${VERSION} to ${HOST}:${DIR}..."
# Unpacked beside the old one and swapped in, so a file a release removed
# (a migration, say) does not linger; config/ comes along, the previous
# code stays in DIR.previous until the next time.
if [[ "$UPGRADE" == true ]]; then
    echo "Backing blt up on ${HOST} first..."
    remote "cd '${DIR}' && ./deploy/backup.sh"
fi
git archive --format=tar HEAD | remote "rm -rf '${DIR}.new' && mkdir -p '${DIR}.new' \
&& tar -x -C '${DIR}.new' && echo '${VERSION}' > '${DIR}.new/VERSION' \
&& { [ ! -d '${DIR}/config' ] || cp -a '${DIR}/config/.' '${DIR}.new/config/'; } \
&& rm -rf '${DIR}.previous' && { [ ! -d '${DIR}' ] || mv '${DIR}' '${DIR}.previous'; } \
&& mv '${DIR}.new' '${DIR}'"
# Settings go through ssh's input, never on a command line. The UI's lines
# are appended: the file is read as shell, so the last setting of a name
# wins.
if [[ "$UPGRADE" == true ]]; then
    :   # the settings there stay as they are
elif remote "test -f '${DIR}/config/blt.env'"; then
    echo "${HOST}:${DIR}/config/blt.env is there already: kept as it is."
else
    {
        tr -d '\r' < "$BLT_ENV_FILE"
        printf '\n# Added by deploy/move.sh from %s, %s\n' "$(hostname)" "$(date +%F)"
        printf '%s\n' "BLT_UI_BIND_ADDRESS=${UI_ADDRESS_THERE}" "BLT_UI_PORT=${UI_PORT_THERE}" \
            "BLT_UI_ORIGIN=${UI_ORIGIN_THERE}"
    } | remote "umask 077 && mkdir -p '${DIR}/config' && cat > '${DIR}/config/blt.env'"
fi

# --- 3. The images: built from this checkout (only the build - nothing
# is started here; podman's cache makes it quick when nothing changed),
# then sent, each only if HOST has a different one.
build_images
for image in "${IMAGES[@]}"; do
    here="$("$PODMAN" image inspect --format '{{.Id}}' "$image" 2>/dev/null)" || {
        echo "No ${image} here: run ./deploy/up.sh once first (Postgres comes from it)." >&2
        exit 1
    }
    there="$(remote "podman image inspect --format '{{.Id}}' ${image} 2>/dev/null" || true)"
    if [[ "$here" == "$there" ]]; then
        echo "${HOST} already has ${image}."
    else
        echo "Copying ${image} to ${HOST}..."
        "$PODMAN" save --format docker-archive "$image" | gzip -1 | remote "gunzip | podman load" >/dev/null
    fi
done

# --- Upgrade: the same start as deploy/up.sh's, on
# the images just sent; collectors there restarted on the new worker image.
if [[ "$UPGRADE" == true ]]; then
    remote "cd '${DIR}' && BLT_SKIP_BUILD=true ./deploy/up.sh"
    remote "for name in \$(podman ps -a --format '{{.Names}}' | grep '^blt-collector-'); do \
podman run -d --replace --name \$name --network host --restart unless-stopped \
--env-file ~/.config/blt/collector-\${name#blt-collector-}.env ${COLLECTOR_IMAGE} >/dev/null \
&& echo \"Collector \${name#blt-collector-} restarted on the new image.\"; done"
    echo "blt ${VERSION} is running on ${HOST}."
    exit 0
fi

# --- 4. The data. Dumped here into a private folder, read back to check
# it, sent, and removed here when this script ends.
DUMPS="$(mktemp -d)"
chmod 700 "$DUMPS"
trap 'rm -rf "$DUMPS"' EXIT
for db in blt prefect; do
    echo "Dumping ${db}..."
    "$PODMAN" exec blt-postgres pg_dump -Fc -U blt -d "$db" > "$DUMPS/$db.dump"
    "$PODMAN" exec -i blt-postgres pg_restore -f /dev/null < "$DUMPS/$db.dump" > /dev/null
done

# --- 5. Start blt there (empty), then put the data in with nothing using
# it, then start it again on that data.
echo "Starting blt on ${HOST} (the first start makes its databases)..."
remote "cd '${DIR}' && BLT_SKIP_BUILD=true ./deploy/up.sh"
remote "podman stop blt-ui blt-prefect-worker blt-prefect-server blt-api >/dev/null"
for db in blt prefect; do
    echo "Restoring ${db} on ${HOST}..."
    remote "podman exec -i blt-postgres pg_restore --clean --if-exists --exit-on-error -U blt -d ${db}" \
        < "$DUMPS/$db.dump"
done
remote "cd '${DIR}' && BLT_SKIP_BUILD=true ./deploy/up.sh"

# --- 6. A collector already on HOST: to blt's Prefect and API there,
# directly, not through the tunnel from this machine.
if [[ -n "$ADOPT" ]]; then
    settings="~/.config/blt/collector-${ADOPT}.env"
    remote "sed -i -e 's#^PREFECT_API_URL=.*#PREFECT_API_URL=http://${PREFECT_LOCAL}:${PREFECT_PORT}/api#' \
-e 's#^BLT_API_URL=.*#BLT_API_URL=http://${LOCAL}:${API_PORT}#' ${settings} && \
podman run -d --replace --name blt-collector-${ADOPT} --network host --restart unless-stopped \
--env-file ${settings} ${COLLECTOR_IMAGE} >/dev/null"
    echo "Collector ${ADOPT} now uses the blt beside it on ${HOST}."
fi

cat <<EOF

blt ${VERSION} is running on ${HOST}, with this machine's data, at
    ${UI_ORIGIN_THERE}
(the same account and password as here).

Next:
  - Here: ./deploy/down.sh            stop this copy (its data is kept)
EOF
[[ -n "$ADOPT" ]] && echo "  - Here: deploy/collector.sh drop-tunnel ${HOST}   the tunnel is not needed now"
cat <<EOF
  - On ${HOST}: cd ${DIR} && ./deploy/backup.sh install-timer
  - On ${HOST}: loginctl enable-linger \$USER   (if it is not on: blt keeps running after you log out)
EOF
