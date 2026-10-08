"""CommvaultClient against mocked HTTP: the login handshake, the shape of
the job query, paging, and the per-job lookup."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx
from sdk_primer import AuthenticationError

from blt.commvault.client import CommvaultClient

BASE = "https://cs.test/commandcenter/api"


def _summary(job_id: int, status: str = "Completed", **extra: object) -> dict[str, object]:
    return {
        "jobSummary": {
            "jobId": job_id,
            "status": status,
            "jobType": "Backup",
            "localizedOperationName": "Backup",
            "backupLevelName": "Incremental",
            "appTypeName": "File System",
            "percentComplete": 100,
            "jobStartTime": 1_700_000_000,
            "jobEndTime": 1_700_000_600,
            "sizeOfApplication": 5_000_000_000,
            "subclient": {"clientId": 7, "clientName": "web01", "subclientName": "default"},
            "storagePolicy": {"storagePolicyName": "SP-Disk"},
            **extra,
        }
    }


def _client(page_size: int = 2) -> CommvaultClient:
    return CommvaultClient(BASE, "svc_blt", "s3cret", page_size=page_size)


@respx.mock
def test_login_then_pages_until_a_short_page() -> None:
    login = respx.post(f"{BASE}/Login").respond(json={"userName": "svc_blt", "token": "QSDK abc"})
    jobs = respx.post(f"{BASE}/Jobs").mock(
        side_effect=[
            httpx.Response(
                200, json={"totalRecordsWithoutPaging": 3, "jobs": [_summary(1), _summary(2)]}
            ),
            httpx.Response(
                200,
                json={
                    "totalRecordsWithoutPaging": 3,
                    "jobs": [_summary(3, "Running", jobEndTime=0)],
                },
            ),
        ]
    )

    with _client() as client:
        pages = list(client.jobs.iter_pages(lookup_seconds=3600))

    assert [[job.job_id for job in page] for page in pages] == [[1, 2], [3]]

    # Logged in once, with the password base64-encoded.
    assert login.call_count == 1
    sent_login = json.loads(login.calls[0].request.content)
    assert sent_login["username"] == "svc_blt"
    assert base64.b64decode(sent_login["password"]).decode() == "s3cret"

    # The token goes out verbatim in Authtoken, and JSON is asked for.
    first = jobs.calls[0].request
    assert first.headers["Authtoken"] == "QSDK abc"
    assert first.headers["Accept"] == "application/json"

    first_body, second_body = (json.loads(call.request.content) for call in jobs.calls)
    assert first_body["jobFilter"]["completedJobLookupTime"] == 3600
    assert first_body["jobFilter"]["showAgedJobs"] is True
    assert first_body["pagingConfig"] == {
        "sortField": "jobId",
        "sortDirection": 0,
        "offset": 0,
        "limit": 2,
    }
    assert second_body["pagingConfig"]["offset"] == 2

    # Translated into blt's shape, raw summary preserved.
    job = pages[0][0]
    assert job.client_name == "web01"
    assert job.storage_policy == "SP-Disk"
    assert job.start_time is not None and job.start_time.timestamp() == 1_700_000_000
    assert job.raw["appTypeName"] == "File System"
    # 0 means "not finished yet", not 1970.
    assert pages[1][0].end_time is None


@respx.mock
def test_access_token_is_sent_as_bearer_with_no_login_call() -> None:
    login = respx.post(f"{BASE}/Login").respond(json={"token": "QSDK unused"})
    info = respx.get(f"{BASE}/CommServ").respond(json={"hostName": "CS", "csVersionInfo": "11"})

    with CommvaultClient(BASE, access_token="tok123") as client:
        assert client.commcell.info()["hostName"] == "CS"

    assert login.call_count == 0
    assert info.calls[0].request.headers["Authtoken"] == "Bearer tok123"


def test_some_credential_is_required() -> None:
    with pytest.raises(ValueError, match="access_token"):
        CommvaultClient(BASE)


@respx.mock
def test_rejected_login_raises_instead_of_sending_no_token() -> None:
    respx.post(f"{BASE}/Login").respond(
        json={"errList": [{"errLogMessage": "Invalid username or password"}]}
    )
    jobs = respx.post(f"{BASE}/Jobs").respond(json={"jobs": []})

    with _client() as client, pytest.raises(AuthenticationError, match="Invalid username"):
        list(client.jobs.iter_pages(lookup_seconds=60))
    assert jobs.call_count == 0


@respx.mock
def test_expired_token_logs_in_again() -> None:
    login = respx.post(f"{BASE}/Login").mock(
        side_effect=[
            httpx.Response(200, json={"token": "QSDK old"}),
            httpx.Response(200, json={"token": "QSDK new"}),
        ]
    )
    job = respx.get(f"{BASE}/Job/5").mock(
        side_effect=[
            httpx.Response(200, json={"jobs": [_summary(5)]}),
            httpx.Response(401),
            httpx.Response(200, json={"jobs": [_summary(5)]}),
        ]
    )

    with _client() as client:
        client.jobs.get(5)
        client.jobs.get(5)

    assert login.call_count == 2
    assert job.calls[-1].request.headers["Authtoken"] == "QSDK new"


@respx.mock
def test_get_job_returns_none_when_the_commcell_has_no_such_job() -> None:
    respx.post(f"{BASE}/Login").respond(json={"token": "QSDK abc"})
    respx.get(f"{BASE}/Job/8").respond(json={"totalRecordsWithoutPaging": 0})
    respx.get(f"{BASE}/Job/9").respond(404)

    with _client() as client:
        assert client.jobs.get(8) is None
        assert client.jobs.get(9) is None


@respx.mock
def test_a_history_slice_asks_for_finished_jobs_in_an_end_time_range() -> None:
    from datetime import UTC, datetime

    respx.post(f"{BASE}/Login").respond(json={"token": "QSDK abc"})
    jobs = respx.post(f"{BASE}/Jobs").respond(json={"jobs": [_summary(4)]})
    low = datetime(2026, 9, 1, tzinfo=UTC)
    high = datetime(2026, 9, 2, tzinfo=UTC)

    with _client() as client:
        pages = list(client.jobs.iter_pages(86_400, ended_between=(low, high)))
        oldest = client.jobs.oldest_start(86_400)

    assert [job.job_id for job in pages[0]] == [4]
    sliced = json.loads(jobs.calls[0].request.content)
    assert sliced["category"] == 2  # finished only
    assert sliced["jobFilter"]["endTimeRange"] == {
        "fromTime": int(low.timestamp()),
        "toTime": int(high.timestamp()),
    }
    probe = json.loads(jobs.calls[1].request.content)
    assert probe["pagingConfig"]["limit"] == 1 and probe["pagingConfig"]["sortDirection"] == 0
    assert oldest is not None and oldest.timestamp() == 1_700_000_000


@respx.mock
def test_one_unreadable_job_does_not_stop_the_collection(log_messages: list[str]) -> None:
    respx.post(f"{BASE}/Login").respond(json={"token": "QSDK abc"})
    bad = {"jobSummary": {"jobId": 2, "status": "Completed", "jobStartTime": "not-a-time"}}
    respx.post(f"{BASE}/Jobs").respond(json={"jobs": [_summary(1), bad, _summary(3)]})

    with _client(page_size=10) as client:
        pages = list(client.jobs.iter_pages(lookup_seconds=60))

    assert [job.job_id for job in pages[0]] == [1, 3]
    assert any("Skipping job 2" in message for message in log_messages)
