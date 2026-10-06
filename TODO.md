# TODO

Open items, roughly in the order they matter. Struck through when done.

## Needs a live CommServe to settle

1. ~~**Test renewing an access token *after* it has expired.**~~
   **Done 2026-10-05**, by the first real renewal (`lab/10-setup.sh`,
   about four hours after the token was created, two after it expired).
   - Renewal after expiry works.
   - The response has the assumed fields: the new pair and expiry were
     parsed and written back to `config/cv-toaster.env`.
   - `renewableUntil` did not move (still 2026-12-05), so the 60 days
     look fixed from when the token was first created. One data point.

   Not yet known: whether the expired token in the `Authtoken` header was
   accepted on the renewal call or the no-header retry was what worked.

2. **Check the job listing against real jobs.** Partly done 2026-10-05:
   six real jobs (download, sync, four backups ending Completed, Failed
   and Killed) collected with the right type, level, subclient, sizes and
   failure reason, and no `unknown` states. Still to see against the real
   CommServe: a job caught while Suspended/Running and followed to its
   end, the reconcile pass, paging past one page, and incrementals.

3. **Confirm whether the token lifetime (2 h) is configurable** when a
   token is created, and what the right value is for a scheduled
   collector.

## Collector

4. **Decide what "as far back as it can" should mean on a large
   CommCell.** A first run asks for ten years in one paged query; fine
   for a lab, worth chunking by time window if a production CommServe
   times out on it.
5. **Job detail beyond the summary** (`GET /Job/{id}` details, failure
   reasons, per-attempt info) - not collected today; only `jobSummary`.
6. **Certificate pinning** as an alternative to `CV_VERIFY_TLS=false`
   for CommServes with a self-signed certificate whose name doesn't
   match (the eval OVA).

## API / database

7. **Stale `running` rows in `collection_run`.** A collector killed
   outright (power loss, `kill -9`) never reports its run as failed. It
   doesn't affect the watermark, but nothing marks the run abandoned.
8. **Read-side endpoints** for whatever consumes this data next
   (reporting, alerting) - today the API has only what the collector
   itself needs.

## Operations

9. **A systemd timer unit** instead of the cron line in the README.
10. **Backups of the `blt-pgdata` volume.**
