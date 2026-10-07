#!/bin/bash
# PID 1 of a lab client container. Environment (set by lab/cvlab.py):
#   LAB_CLIENT_NAME   name this client registers under, e.g. blt-lab-01
#   LAB_CS_NAME       the CommServe's client name
#   LAB_CS_HOST       the CommServe's host name (must resolve in here)
#   LAB_AUTHCODE      CommCell install authcode
# /media is the unpacked Commvault Unix install media, read-only.
set -u
LOG=/var/log/lab-install.log

# One or two small files to back up; lab/20-noise.sh appends to them
# between rounds so incrementals have something to pick up.
if [[ ! -e /data/notes.txt ]]; then
    echo "blt lab data on ${LAB_CLIENT_NAME:-unknown}, created $(date -u +%FT%TZ)" > /data/notes.txt
    head -c 262144 /dev/urandom > /data/blob.bin
fi

# There is no systemd in here, and Commvault's control script hands
# "start" to systemctl unless this marker file (its own switch for that)
# exists. Without it the agent installs and registers but never runs, and
# every backup sits Pending with "No direct tunnel to <client>".
touch /tmp/cvpkgadd_unlock_nosystemd_nosysv

if command -v commvault >/dev/null 2>&1; then
    # Restarted container: the agent is installed, just not running.
    commvault start >>"$LOG" 2>&1 || true
else
    : "${LAB_CLIENT_NAME:?}" "${LAB_CS_NAME:?}" "${LAB_CS_HOST:?}" "${LAB_AUTHCODE:?}"
    # The installer unpacks next to itself, so it needs a writable copy.
    cp -r /media/Unix /tmp/cvmedia
    # Commvault's own answer file already asks for "client opens the
    # connection to the CommServe" (firewallConnectionType 0), which is
    # what a container with no reachable address needs. Fill in the blanks.
    sed -e "s|<CommserveHostInfo clientName=\"\" hostName=\"\" />|<CommserveHostInfo clientName=\"${LAB_CS_NAME}\" hostName=\"${LAB_CS_HOST}\" />|" \
        -e "s|<clientEntity clientName=\"\" hostName=\"\" />|<clientEntity clientName=\"${LAB_CLIENT_NAME}\" hostName=\"${LAB_CLIENT_NAME}\" />|" \
        -e "s|<organizationProperties authCode=\"\" />|<organizationProperties authCode=\"${LAB_AUTHCODE}\" />|" \
        /tmp/cvmedia/default.xml > /tmp/install.xml
    echo "$(date -u +%FT%TZ) installing as ${LAB_CLIENT_NAME} against ${LAB_CS_HOST}" >>"$LOG"
    # The installer redraws one progress line thousands of times; keep
    # that out of the container log and report only how it ended.
    (cd /tmp/cvmedia && ./silent_install -p /tmp/install.xml) >>"$LOG" 2>&1
    rc=$?
    rm -rf /tmp/cvmedia /tmp/install.xml
    # Exit 0 means installed *and* registered. The agent can be fully
    # installed and still have failed to register (exit 59).
    if [[ $rc -eq 0 ]] && command -v commvault >/dev/null 2>&1; then
        commvault start >>"$LOG" 2>&1 || true
        echo "LAB: install finished (exit $rc)"
        commvault status 2>&1 | grep -E "Version|CommServe|Name =" | sed 's/^/LAB: /'
    else
        echo "LAB: INSTALL FAILED (exit $rc) - last lines of $LOG:"
        tr '\r' '\n' <"$LOG" | grep -v "^ - Installing" | tail -20
    fi
fi

# Stay up either way, so a failed install can be inspected in place.
#
# This shell stays as PID 1 rather than exec-ing sleep, because PID 1 is
# who inherits every daemon the agent forks, and something has to reap
# them when they exit. With nothing reaping, stopped Commvault services
# linger as zombies, `commvault start` sees their names in the process
# table, reports "All services started" and starts nothing - which is how
# a CommServe-initiated update left a client permanently offline.
while true; do
    sleep 30 &
    wait $!
done
