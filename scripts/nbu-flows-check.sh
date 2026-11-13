#!/usr/bin/env bash
# Can blt draw NetBackup's Sankey from what it has collected? Read-only:
# the session is read-only, and nothing is changed anywhere.
#
#   scripts/nbu-flows-check.sh [days]     (default 7)
#
# Run on the machine blt's pod runs on, as the user that runs it. Prints
# four tables; scripts/README.md says how to read them. Paste the output
# back if something looks off - it holds job counts and storage unit
# names, no credentials.
set -euo pipefail
DAYS="${1:-7}"
if [[ ! "$DAYS" =~ ^[1-9][0-9]*$ ]]; then
    echo "usage: $0 [days]" >&2
    exit 64
fi

podman exec -i -e PGOPTIONS="-c default_transaction_read_only=on" "${BLT_PG_CONTAINER:-blt-postgres}" \
    psql -U blt -d blt -v ON_ERROR_STOP=1 -v days="$DAYS" -P pager=off <<'SQL'
\echo '1. Jobs per primary and type that ended in the window, and how many carry a backup id'
SELECT p.name AS primary_server, j.job_type, count(*) AS jobs,
       count(j.backup_id) AS with_backup_id,
       count(*) FILTER (WHERE j.state = 'DONE' AND j.status IN (0, 1)) AS done_ok,
       pg_size_pretty(sum(coalesce(j.kilobytes_transferred, 0)) * 1024) AS written
FROM netbackup.job j JOIN netbackup.primary_server p ON p.id = j.primary_id
WHERE j.end_time > now() - make_interval(days => :days)
  AND j.job_type IN ('BACKUP', 'DBBACKUP', 'ARCHIVE', 'DUPLICATE', 'REPLICATE', 'IMPORT', 'REPLICA_IMPORT')
GROUP BY 1, 2 ORDER BY 1, 2;

\echo '2. Replications, and how many are matched to an import on another primary'
SELECT p.name AS from_primary, coalesce(imp.primary_name, '(not matched)') AS to_primary,
       count(*) AS replications
FROM netbackup.job j
JOIN netbackup.primary_server p ON p.id = j.primary_id
LEFT JOIN LATERAL (
    SELECT ip.name AS primary_name
    FROM netbackup.job i JOIN netbackup.primary_server ip ON ip.id = i.primary_id
    WHERE i.backup_id = j.backup_id AND i.primary_id <> j.primary_id
      AND i.job_type IN ('IMPORT', 'REPLICA_IMPORT')
    LIMIT 1
) imp ON true
WHERE j.job_type = 'REPLICATE' AND j.end_time > now() - make_interval(days => :days)
GROUP BY 1, 2 ORDER BY 1, 2;

\echo '3. Storage units jobs wrote to that are in no collected disk pool'
SELECT p.name AS primary_server, j.storage_unit, count(*) AS jobs
FROM netbackup.job j
JOIN netbackup.primary_server p ON p.id = j.primary_id
WHERE j.end_time > now() - make_interval(days => :days)
  AND j.job_type IN ('BACKUP', 'DBBACKUP', 'ARCHIVE', 'DUPLICATE', 'IMPORT', 'REPLICA_IMPORT')
  AND j.storage_unit IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM common.storage s JOIN common.source src ON src.id = s.source_id
      WHERE src.type = 'netbackup' AND src.name = p.name AND s.present
        AND s.attributes -> 'storage_units' ? j.storage_unit
  )
GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo '4. A few replication jobs as NetBackup gave them (storage units and ids only)'
SELECT p.name AS primary_server, j.job_id, j.backup_id, j.source_storage_unit, j.storage_unit,
       j.raw ->> 'destinationStorageUnitName' AS destination_unit,
       j.raw ->> 'destinationMediaServerName' AS destination_media_server
FROM netbackup.job j JOIN netbackup.primary_server p ON p.id = j.primary_id
WHERE j.job_type = 'REPLICATE' AND j.end_time > now() - make_interval(days => :days)
ORDER BY j.end_time DESC LIMIT 5;
SQL
