#!/bin/bash
# PID 1 of a lab SQL Server container. Environment (set by lab/cvlab.py):
#   LAB_SA_PASSWORD   password for SQL Server's sa login
#   LAB_DB_COUNT      how many user databases to create on first start
#   LAB_SKIP_AGENT    if set, stop after SQL Server is up (no Commvault)
#   LAB_CLIENT_NAME, LAB_CS_NAME, LAB_CS_HOST, LAB_AUTHCODE
#                     as in lab/client-image/entrypoint.sh
set -u
LOG=/var/log/lab-install.log
: "${LAB_SA_PASSWORD:?}"

stay_up() { while true; do sleep 30 & wait $!; done; }

sql() { sqlcmd -C -S localhost -U sa -P "$LAB_SA_PASSWORD" -b -h -1 -W "$@"; }

# --- SQL Server. No systemd in here, so sqlservr is started directly, as
# the mssql user the package created. On first start these variables are
# what configure it; afterwards they are ignored.
# Required for any backup tool to work against SQL Server in a container:
# backups are handed over through shared memory (the VDI interface), and
# SQL Server only does that in a container when this is on (Microsoft's
# "Configure SQL Server containers" page). Without it every backup dies
# at "OpenDevice Failed [0x80770004]" with OS error 995. It also needs a
# /dev/shm bigger than the 64 MB default - see --shm-size in lab/cvlab.py.
/opt/mssql/bin/mssql-conf set memory.enablecontainersharedmemory true >/dev/null 2>&1
chown mssql:mssql /var/opt/mssql/mssql.conf 2>/dev/null

runuser -u mssql -- env ACCEPT_EULA=Y MSSQL_PID=Developer \
    MSSQL_SA_PASSWORD="$LAB_SA_PASSWORD" /opt/mssql/bin/sqlservr >/var/log/sqlservr.log 2>&1 &

for _ in $(seq 1 60); do
    sql -Q "select 1" >/dev/null 2>&1 && break
    sleep 2
done
if ! sql -Q "select 1" >/dev/null 2>&1; then
    echo "LAB: SQL SERVER DID NOT START - last lines of /var/log/sqlservr.log:"
    tail -15 /var/log/sqlservr.log
    stay_up
fi

# --- Databases to protect, created once.
if [[ "$(sql -Q "set nocount on; select count(*) from sys.databases where name like 'lab_db_%'")" == "0" ]]; then
    for n in $(seq -w 1 "${LAB_DB_COUNT:-10}"); do
        sql -Q "create database lab_db_$n; alter database lab_db_$n set recovery full;" >/dev/null
        sql -d "lab_db_$n" -Q "create table notes (id int identity primary key, line nvarchar(200), at datetime2 default sysutcdatetime()); insert notes (line) values ('created');" >/dev/null
    done
fi
echo "LAB: SQL Server is up with $(sql -Q "set nocount on; select count(*) from sys.databases where database_id > 4") user databases"

if [[ -n "${LAB_SKIP_AGENT:-}" ]]; then
    stay_up
fi

# --- Commvault agent: same shape as lab/client-image/entrypoint.sh.
touch /tmp/cvpkgadd_unlock_nosystemd_nosysv
if command -v commvault >/dev/null 2>&1; then
    commvault start >>"$LOG" 2>&1 || true
else
    : "${LAB_CLIENT_NAME:?}" "${LAB_CS_NAME:?}" "${LAB_CS_HOST:?}" "${LAB_AUTHCODE:?}"
    cp -r /media/Unix /tmp/cvmedia
    sed -e "s|<CommserveHostInfo clientName=\"\" hostName=\"\" />|<CommserveHostInfo clientName=\"${LAB_CS_NAME}\" hostName=\"${LAB_CS_HOST}\" />|" \
        -e "s|<clientEntity clientName=\"\" hostName=\"\" />|<clientEntity clientName=\"${LAB_CLIENT_NAME}\" hostName=\"${LAB_CLIENT_NAME}\" />|" \
        -e "s|<organizationProperties authCode=\"\" />|<organizationProperties authCode=\"${LAB_AUTHCODE}\" />|" \
        /tmp/cvmedia/default.xml > /tmp/install.xml
    echo "$(date -u +%FT%TZ) installing as ${LAB_CLIENT_NAME} against ${LAB_CS_HOST}" >>"$LOG"
    (cd /tmp/cvmedia && ./silent_install -p /tmp/install.xml) >>"$LOG" 2>&1
    rc=$?
    rm -rf /tmp/cvmedia /tmp/install.xml
    if [[ $rc -eq 0 ]] && command -v commvault >/dev/null 2>&1; then
        commvault start >>"$LOG" 2>&1 || true
        echo "LAB: install finished (exit $rc)"
        commvault status 2>&1 | grep -E "Version|CommServe|Name =|SQL" | sed 's/^/LAB: /'
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
