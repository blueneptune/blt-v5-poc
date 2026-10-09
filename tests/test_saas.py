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


# --- A managed service provider: one login, many customer tenants.


def _msp_config(tmp_path: Path) -> None:
    (tmp_path / "blt.env").write_text("BLT_API_KEY=k\n")
    (tmp_path / "msp.env").write_text(
        "CV_BASE_URL=https://api.metallic.io\nCV_DEPLOYMENT=saas\n"
        "CV_ACCESS_TOKEN=shared-access\nCV_REFRESH_TOKEN=shared-refresh\n"
    )
    (tmp_path / "acme.env").write_text("CV_CREDENTIALS=msp\nCV_ACCOUNT_ID=1001\n")
    (tmp_path / "globex.env").write_text(
        "CV_CREDENTIALS=msp\nCV_TENANT_GUID=AAAA-BBBB\nCV_RING_ID=M301\n"
    )


def test_a_tenant_file_borrows_the_msp_credentials_and_names_its_tenant(tmp_path: Path) -> None:
    from blt.collector.settings import credentials_env_path

    _msp_config(tmp_path)
    acme = load_settings("acme", tmp_path)
    assert acme.cv_base_url == "https://api.metallic.io" and acme.cv_deployment == "saas"
    assert acme.cv_access_token.get_secret_value() == "shared-access"  # type: ignore[union-attr]
    assert acme.tenant_headers == {"lhAccountId": "1001"}
    assert load_settings("globex", tmp_path).tenant_headers == {
        "tenantGuid": "AAAA-BBBB",
        "ring-id": "M301",
    }
    # Renewed tokens go back to the shared file, not the tenant's.
    assert credentials_env_path(acme, "acme", tmp_path) == tmp_path / "msp.env"
    # A standalone customer sends no tenant header at all.
    assert load_settings("msp", tmp_path).tenant_headers == {}


@respx.mock
def test_the_tenant_header_rides_on_data_calls_but_not_on_renewal(tmp_path: Path) -> None:
    from blt.collector.connect import commvault_client

    _msp_config(tmp_path)

    def jobs(request: httpx.Request) -> httpx.Response:
        ok = request.headers.get("Authorization") == "Bearer new-access"
        return httpx.Response(200 if ok else 401, json={"jobs": []})

    listing = respx.post(f"{SAAS}/Jobs").mock(side_effect=jobs)
    renew = respx.post(f"{SAAS}/V4/AccessToken/Renew").respond(
        json={"accessToken": "new-access", "refreshToken": "new-refresh"}
    )

    with commvault_client(load_settings("acme", tmp_path), "acme", tmp_path) as cv:
        cv.jobs.sample(3600)

    assert listing.calls[-1].request.headers["lhAccountId"] == "1001"
    assert "lhAccountId" not in renew.calls[0].request.headers
    # The new pair is written to the shared file, for every tenant's next run.
    saved = (tmp_path / "msp.env").read_text()
    assert "CV_ACCESS_TOKEN=new-access" in saved and "CV_REFRESH_TOKEN=new-refresh" in saved
    assert "new-access" not in (tmp_path / "acme.env").read_text()


@respx.mock
def test_a_second_tenant_run_adopts_a_renewal_instead_of_repeating_it(tmp_path: Path) -> None:
    from blt.collector.connect import commvault_client

    _msp_config(tmp_path)
    respx.post(f"{SAAS}/Jobs").mock(
        side_effect=lambda request: httpx.Response(
            200 if request.headers.get("Authorization") == "Bearer new-access" else 401,
            json={"jobs": []},
        )
    )
    renew = respx.post(f"{SAAS}/V4/AccessToken/Renew").respond(
        json={"accessToken": "new-access", "refreshToken": "new-refresh"}
    )

    # Both runs start with the old pair in memory...
    acme = commvault_client(load_settings("acme", tmp_path), "acme", tmp_path)
    globex = commvault_client(load_settings("globex", tmp_path), "globex", tmp_path)
    with acme, globex:
        acme.jobs.sample(3600)  # ...acme renews it,
        globex.jobs.sample(3600)  # ...and globex must not trade in the dead pair.

    assert renew.call_count == 1


@respx.mock
def test_partner_accounts_listing() -> None:
    respx.get(f"{SAAS}/api/v1/partner/accounts").respond(
        json={"data": [{"id": "1001", "name": "Acme"}, {"id": "1002", "name": "Globex"}]}
    )
    with CommvaultClient(SAAS, access_token="tok", deployment="saas") as cv:
        assert [a["id"] for a in cv.partner.accounts()] == ["1001", "1002"]
