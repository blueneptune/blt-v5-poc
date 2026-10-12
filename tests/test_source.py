"""The one place an SDK Job becomes blt's JobIn."""

from __future__ import annotations

import respx
from commvault_sdk import CommvaultClient

from blt.collector.source import CommvaultJobs
from blt.schemas import JobIn

BASE = "https://cs.test/commandcenter/api"
SUMMARY = {
    "jobId": 7,
    "status": "Completed",
    "jobType": "Backup",
    "backupLevelName": "Full",
    "jobStartTime": 1_700_000_000,
    "jobEndTime": 1_700_000_600,
    "subclient": {"clientId": 3, "clientName": "web01", "subclientName": "default"},
}


@respx.mock
def test_sdk_jobs_reach_the_collector_as_blt_job_ins() -> None:
    respx.post(f"{BASE}/Jobs").respond(json={"jobs": [{"jobSummary": SUMMARY}]})
    respx.get(f"{BASE}/Job/7").respond(json={"jobs": [{"jobSummary": SUMMARY}]})
    respx.get(f"{BASE}/Job/8").respond(json={})

    with CommvaultClient(BASE, access_token="tok") as cv:
        source = CommvaultJobs(cv)
        (page,) = list(source.iter_pages(3600))
        one = source.get(7)
        assert source.get(8) is None

    job = page[0]
    assert type(job) is JobIn and type(one) is JobIn
    assert (job.job_id, job.status, job.client_name, job.backup_level) == (
        7,
        "Completed",
        "web01",
        "Full",
    )
    assert job.start_time is not None and job.start_time.timestamp() == 1_700_000_000
    # Commvault's own record travels with it, untouched.
    assert job.raw == SUMMARY
    assert one == job
