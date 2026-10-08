"""The Commvault SDK facade: the read-only rule, the request each
changing call sends, and how Commvault's in-body errors are surfaced."""

from __future__ import annotations

import base64
import json

import pytest
import respx
from sdk_primer import SDKError

from blt.commvault import ChangesNotAllowed, CommvaultClient, CommvaultError

BASE = "https://cs.test/commandcenter/api"
OK = {"response": [{"errorCode": 0}]}


def _body(route: respx.Route) -> dict:
    return json.loads(route.calls[0].request.content)


@respx.mock
def test_a_default_client_refuses_every_call_that_changes_the_commserve() -> None:
    # No routes are mocked: reaching the network at all would fail the test.
    with CommvaultClient(BASE, access_token="tok") as cv:
        changes = [
            lambda: cv.jobs.kill(1),
            lambda: cv.jobs.suspend(1),
            lambda: cv.jobs.resume(1),
            lambda: cv.subclients.create("c", "s", content=[{"path": "/data"}]),
            lambda: cv.subclients.backup(1, "Full"),
            lambda: cv.subclients.set_plan(1, {"planName": "p"}),
            lambda: cv.subclients.delete(1),
            lambda: cv.clients.delete(1),
            lambda: cv.instances.update({"instanceId": 1}, {}),
            lambda: cv.sql.set_instance_credential({"instanceId": 1}, "cred"),
            lambda: cv.credentials.create("n", "sa", "pw"),
            lambda: cv.commcell.install_authcode(),
        ]
        for change in changes:
            with pytest.raises(ChangesNotAllowed, match="read-only"):
                change()
    # It is an SDKError, so the collector's existing handling covers it.
    assert issubclass(ChangesNotAllowed, SDKError)


@respx.mock
def test_reads_work_on_a_read_only_client() -> None:
    respx.get(f"{BASE}/CommServ").respond(json={"hostName": "CS"})
    respx.get(f"{BASE}/V4/Plan/Summary").respond(
        json={"plans": [{"plan": {"name": "Standard Plan", "id": 1}}]}
    )
    respx.get(f"{BASE}/V4/Storage/Disk").respond(json={"diskStorage": [{"name": "Pool"}]})
    respx.get(f"{BASE}/Client").respond(
        json={
            "clientProperties": [{"client": {"clientEntity": {"clientId": 2, "clientName": "CS"}}}]
        }
    )
    # "None" comes back as a 404 from some listings.
    respx.get(f"{BASE}/Subclient", params={"clientId": 2}).respond(404)
    respx.post(f"{BASE}/JobDetails").respond(
        json={"job": {"jobDetail": {"detailInfo": {"numOfObjects": 14, "skippedItems": 1}}}}
    )

    with CommvaultClient(BASE, access_token="tok") as cv:
        assert cv.commcell.info() == {"hostName": "CS"}
        assert cv.plans.ids() == {"Standard Plan": 1}
        assert cv.storage.disk() == [{"name": "Pool"}]
        assert cv.clients.find("cs") == {"clientId": 2, "clientName": "CS"}
        assert cv.clients.find("nope") is None
        assert cv.subclients.list(2) == []
        assert cv.jobs.counts(112) == {"job_id": 112, "backed_up": 14, "skipped": 1}


@respx.mock
def test_changing_calls_send_the_requests_the_lab_proved() -> None:
    create = respx.post(f"{BASE}/Subclient").respond(json=OK)
    backup = respx.post(f"{BASE}/Subclient/43/action/backup").respond(json={"jobIds": ["117"]})
    plan = respx.post(f"{BASE}/Subclient/43").respond(json=OK)
    kill = respx.post(f"{BASE}/Job/117/action/kill").respond(json={})
    pause = respx.post(f"{BASE}/Job/117/action/pause").respond(json={})
    remove_client = respx.delete(f"{BASE}/Client/15").respond(json={})
    credential = respx.post(f"{BASE}/Commcell/Credentials").respond(
        json={"error": {"errorCode": 0}}
    )
    instance = respx.post(f"{BASE}/Instance/6").respond(json=OK)
    authcode = respx.post(f"{BASE}/Organization/0/Authtoken").respond(
        json={"organizationProperties": {"authCode": "ABC123"}}
    )

    with CommvaultClient(BASE, access_token="tok", allow_changes=True) as cv:
        cv.subclients.create(
            "sql01",
            "blt-lab-data",
            content=[{"path": "/data"}],
            plan={"planName": "Standard Plan", "planId": 1},
            description="lab",
        )
        assert cv.subclients.backup(43, "Full") == 117
        cv.subclients.set_plan(43, {"planName": "Standard Plan", "planId": 1})
        cv.jobs.suspend(117)
        cv.jobs.kill(117)
        cv.clients.delete(15)
        cv.credentials.create("blt-lab-sql-sa", "sa", "s3cret")
        cv.sql.set_instance_credential({"instanceId": 6, "instanceName": "sql01"}, "blt-lab-sql-sa")
        assert cv.commcell.install_authcode() == "ABC123"

    properties = _body(create)["subClientProperties"]
    assert properties["subClientEntity"] == {
        "clientName": "sql01",
        "appName": "File System",
        "instanceName": "DefaultInstanceName",
        "backupsetName": "defaultBackupSet",
        "subclientName": "blt-lab-data",
    }
    assert properties["content"] == [{"path": "/data"}]
    assert properties["planEntity"] == {"planName": "Standard Plan", "planId": 1}
    assert backup.calls[0].request.url.params["backupLevel"] == "Full"
    assert _body(plan) == {
        "subClientProperties": {"planEntity": {"planName": "Standard Plan", "planId": 1}}
    }
    assert kill.called and pause.called
    assert remove_client.calls[0].request.url.params["forceDelete"] == "1"
    record = _body(credential)["credentialRecordInfo"][0]
    assert record["record"]["userName"] == "sa"
    # Never sent in the clear.
    assert base64.b64decode(record["record"]["password"]).decode() == "s3cret"
    sent = _body(instance)["instanceProperties"]
    assert sent["instance"]["instanceId"] == 6
    assert sent["mssqlInstance"]["MSSQLCredentialinfo"] == {"credentialName": "blt-lab-sql-sa"}
    assert sent["mssqlInstance"]["overrideHigherLevelSettings"]["useLocalSystemAccount"] is False
    assert authcode.called


@respx.mock
def test_an_error_inside_a_200_is_raised_not_ignored() -> None:
    respx.post(f"{BASE}/Subclient").respond(
        json={"response": [{"errorCode": 2, "errorString": "Subclient already exists"}]}
    )
    respx.post(f"{BASE}/Subclient/43/action/backup").respond(json={"errorCode": 0})

    with CommvaultClient(BASE, access_token="tok", allow_changes=True) as cv:
        with pytest.raises(CommvaultError, match="Subclient already exists"):
            cv.subclients.create("c", "s", content=[{"path": "/x"}])
        # A backup that returns no job id did not start.
        with pytest.raises(CommvaultError, match="did not start"):
            cv.subclients.backup(43, "Full")


@respx.mock
def test_a_change_is_never_retried_but_a_read_is() -> None:
    import httpx

    backup = respx.post(f"{BASE}/Subclient/43/action/backup").mock(
        side_effect=httpx.ReadTimeout("slow CommServe")
    )
    read = respx.get(f"{BASE}/CommServ").mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json={"hostName": "CS"})]
    )

    with CommvaultClient(BASE, access_token="tok", allow_changes=True) as cv:
        # Asking again is harmless, so a read gets another go.
        cv.api._sleep = lambda _seconds: None
        assert cv.commcell.info() == {"hostName": "CS"}
        # Starting a backup again is not: the first request may have
        # worked, and a second would start a second job.
        with pytest.raises(SDKError):
            cv.subclients.backup(43, "Full")

    assert read.call_count == 2
    assert backup.call_count == 1
