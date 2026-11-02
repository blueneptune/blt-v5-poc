#!/usr/bin/env bash
# Back up blt's Postgres: dump the blt database (and prefect, with
# BLT_PREFECT=true) from the running blt-postgres container, check each
# dump reads back, and keep the newest BLT_BACKUP_KEEP of each.
#
#   deploy/backup.sh                  # back up now
#   deploy/backup.sh install-timer    # back up every night (systemd user timer)
#
# Settings, in config/blt.env:
#   BLT_BACKUP_DIR    where the dumps go (default ~/blt-backups)
#   BLT_BACKUP_KEEP   nightly dumps kept per database (default 14)
#   BLT_BACKUP_AT     systemd OnCalendar time for the timer (default 02:30)
#
# Nightly dumps are named <database>-nightly-<YYYYMMDD-HHMMSS>.dump and
# only those are pruned: dumps taken by hand before an upgrade
# (blt-before-<tag>.dump) are left alone.
#
# The prefect dump holds Prefect's blocks - CommCell tokens, RSC service
# accounts - so the folder is made private (700) and each dump 600.
#
# Restore one (stops nothing; the API and Prefect reconnect):
#   podman exec -i blt-postgres pg_restore --clean --if-exists -U blt -d blt < FILE
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=deploy/env.sh
source deploy/env.sh

BACKUP_DIR="${BLT_BACKUP_DIR:-$HOME/blt-backups}"
KEEP="${BLT_BACKUP_KEEP:-14}"
AT="${BLT_BACKUP_AT:-02:30}"
CONTAINER=blt-postgres
UNIT=blt-backup

if [[ ! "$KEEP" =~ ^[1-9][0-9]*$ ]]; then
    echo "BLT_BACKUP_KEEP in config/blt.env must be a whole number, 1 or more." >&2
    exit 1
fi

install_timer() {
    if ! systemctl --user show-environment >/dev/null 2>&1; then
        echo "systemd is not running for this user, so there is no timer to install." >&2
        echo "Under WSL, turn it on: add to /etc/wsl.conf" >&2
        echo "    [boot]" >&2
        echo "    systemd=true" >&2
        echo "then 'wsl --shutdown' from PowerShell, reopen WSL and run this again." >&2
        echo "Or run deploy/backup.sh nightly from cron instead:" >&2
        echo "    30 2 * * * ${ROOT}/deploy/backup.sh >> ${BACKUP_DIR}/backup.log 2>&1" >&2
        exit 1
    fi
    local units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$units"
    cat > "$units/$UNIT.service" <<EOF
[Unit]
Description=Back up blt's Postgres (deploy/backup.sh)

[Service]
Type=oneshot
WorkingDirectory=${ROOT}
ExecStart=${ROOT}/deploy/backup.sh
EOF
    # Persistent: a night missed because the machine or WSL was off is
    # made up the next time it starts.
    cat > "$units/$UNIT.timer" <<EOF
[Unit]
Description=Back up blt's Postgres every night

[Timer]
OnCalendar=*-*-* ${AT}
Persistent=true

[Install]
WantedBy=timers.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now "$UNIT.timer"
    systemctl --user list-timers "$UNIT.timer" --no-pager
    if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != yes ]]; then
        echo
        echo "The timer only runs while you are logged in. To run it regardless:"
        echo "    loginctl enable-linger $USER"
    fi
    echo "Logs: journalctl --user -u $UNIT.service"
}

# Dump one database to a .partial file, check pg_restore can read it, then
# give it its name - so a dump that failed half way never counts as one.
dump() {
    local db="$1" stamp="$2"
    local target="$BACKUP_DIR/${db}-nightly-${stamp}.dump"
    if ! podman exec "$CONTAINER" pg_dump -Fc -U blt -d "$db" > "$target.partial"; then
        rm -f "$target.partial"
        echo "pg_dump of ${db} failed." >&2
        return 1
    fi
    if ! podman exec -i "$CONTAINER" pg_restore --list < "$target.partial" > /dev/null; then
        rm -f "$target.partial"
        echo "The dump of ${db} does not read back; not kept." >&2
        return 1
    fi
    mv "$target.partial" "$target"
    echo "$target ($(du -h "$target" | cut -f1))"
}

# Remove all but the newest $KEEP nightly dumps of one database. The
# timestamp in the name sorts in time order.
prune() {
    local db="$1" old
    while IFS= read -r old; do
        rm -f -- "$old"
        echo "removed $old"
    done < <(compgen -G "$BACKUP_DIR/${db}-nightly-*.dump" | sort -r | tail -n "+$((KEEP + 1))")
}

backup() {
    if [[ "$(podman container inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" != true ]]; then
        echo "${CONTAINER} is not running - start blt with ./deploy/up.sh first." >&2
        exit 1
    fi
    umask 077
    mkdir -p "$BACKUP_DIR"
    chmod 700 "$BACKUP_DIR"
    local stamp databases=(blt) db failed=0
    stamp="$(date +%Y%m%d-%H%M%S)"
    [[ "$PREFECT_ENABLED" == true ]] && databases+=(prefect)
    for db in "${databases[@]}"; do
        # A failed dump keeps every older one: prune only after a good one.
        if dump "$db" "$stamp"; then
            prune "$db"
        else
            failed=1
        fi
    done
    return "$failed"
}

case "${1:-}" in
    "") backup ;;
    install-timer) install_timer ;;
    *)
        echo "usage: $0 [install-timer]" >&2
        exit 64
        ;;
esac
