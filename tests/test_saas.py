"""What differs for Commvault Cloud (SaaS): where the token goes, and
the hourly call budget."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from blt.collector.settings import SAAS_CALLS_PER_HOUR, load_settings
from blt.commvault import CommvaultClient, TokenSet
from blt.commvault.budget import CallBudget

SAAS = "https://api.metallic.io"
SOFTWARE = "https://cs.test/commandcenter/api"


@respx.mock
def test_saas_sends_the_token_in_authorization_software_in_authtoken() -> None:
    saas = respx.get(f"{SAAS}/CommServ").respond(json={"hostName": "cloud"})
    software = respx.get(f"{SOFTWARE}/CommServ").respond(json={"hostName": "cs"})

    with CommvaultClient(SAAS, access_token="tok", deployment="saas") as cv:
        cv.commcell.info()
    with CommvaultClient(SOFTWARE, access_token="tok") as cv:
        cv.commcell.info()

    sent = saas.calls[0].request.headers
    assert sent["Authorization"] == "Bearer tok" and "Authtoken" not in sent
    sent = software.calls[0].request.headers
    assert sent["Authtoken"] == "Bearer tok" and "Authorization" not in sent


@respx.mock
def test_saas_renewal_uses_the_same_header() -> None:
    def commserv(request: httpx.Request) -> httpx.Response:
        ok = request.headers.get("Authorization") == "Bearer new"
        return httpx.Response(200 if ok else 401, json={"hostName": "cloud"})

    respx.get(f"{SAAS}/CommServ").mock(side_effect=commserv)
    renew = respx.post(f"{SAAS}/V4/AccessToken/Renew").respond(
        json={"accessToken": "new", "refreshToken": "r2"}
    )
    with CommvaultClient(SAAS, access_token=TokenSet("old", "r1"), deployment="saas") as cv:
        assert cv.commcell.info() == {"hostName": "cloud"}
    assert renew.calls[0].request.headers["Authorization"] == "Bearer old"
    assert json.loads(renew.calls[0].request.content) == {
        "accessToken": "old",
        "refreshToken": "r1",
    }


@respx.mock
def test_saas_password_login_token_is_sent_as_bearer() -> None:
    respx.post(f"{SAAS}/Login").respond(json={"token": "QSDK abc"})
    info = respx.get(f"{SAAS}/CommServ").respond(json={})
    with CommvaultClient(SAAS, "user@example.com", "pw", deployment="saas") as cv:
        cv.commcell.info()
    assert info.calls[0].request.headers["Authorization"] == "Bearer QSDK abc"


def test_the_budget_waits_once_the_hour_is_spent() -> None:
    clock = [0.0]
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock[0] += seconds

    budget = CallBudget(3, now=lambda: clock[0], sleep=sleep)
    for _ in range(3):
        budget.spend()
        clock[0] += 60
    assert waits == [] and budget.used == 3

    # The fourth has to wait until the first is an hour old.
    budget.spend()
    assert len(waits) == 1 and 3400 < waits[0] < 3600
    assert budget.used == 3


@respx.mock
def test_every_request_is_counted_including_changes() -> None:
    respx.get(f"{SAAS}/CommServ").respond(json={})
    respx.post(f"{SAAS}/Jobs").respond(json={"jobs": []})
    respx.post(f"{SAAS}/Job/5/action/kill").respond(json={})
    with CommvaultClient(
        SAAS, access_token="tok", deployment="saas", max_calls_per_hour=100, allow_changes=True
    ) as cv:
        cv.commcell.info()
        cv.jobs.sample(3600)
        cv.jobs.kill(5)
        assert cv.budget is not None and cv.budget.used == 3
    with CommvaultClient(SOFTWARE, access_token="tok") as cv:
        assert cv.budget is None  # no ceiling unless one is set


def test_saas_gets_a_budget_by_default_software_does_not(tmp_path: Path) -> None:
    (tmp_path / "blt.env").write_text("BLT_API_KEY=k\n")
    (tmp_path / "cloud.env").write_text(
        "CV_BASE_URL=https://api.metallic.io\nCV_DEPLOYMENT=saas\nCV_ACCESS_TOKEN=t\n"
    )
    (tmp_path / "onprem.env").write_text("CV_BASE_URL=https://cs/api\nCV_ACCESS_TOKEN=t\n")
    (tmp_path / "tuned.env").write_text(
        "CV_BASE_URL=https://api.metallic.io\nCV_DEPLOYMENT=saas\nCV_ACCESS_TOKEN=t\n"
        "CV_MAX_CALLS_PER_HOUR=300\n"
    )
    assert load_settings("cloud", tmp_path).calls_per_hour == SAAS_CALLS_PER_HOUR
    assert load_settings("onprem", tmp_path).calls_per_hour is None
    assert load_settings("tuned", tmp_path).calls_per_hour == 300
