"""What differs for Commvault Cloud (SaaS): where the token goes, and
the hourly call budget."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx
from commvault_sdk import CommvaultClient

from blt.collector.cli import describe_login
from blt.collector.settings import SAAS_CALLS_PER_HOUR, load_settings

SAAS = "https://api.metallic.io"
SOFTWARE = "https://cs.test/commandcenter/api"


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


JOBS = {"totalRecordsWithoutPaging": 334, "jobs": []}


@respx.mock
def test_a_saas_check_lists_jobs_and_never_asks_for_commserve_details() -> None:
    # Commvault Cloud answers GET /CommServ with 403 for tenant and MSP
    # users. It is not mocked here: asking for it would fail the test.
    jobs = respx.post("https://api.metallic.io/Jobs").respond(json=JOBS)
    with CommvaultClient("https://api.metallic.io", access_token="t", deployment="saas") as cv:
        assert describe_login(cv, saas=True) == "334 jobs visible in the last 24 hours"
    assert jobs.call_count == 1


@respx.mock
def test_a_software_check_falls_back_to_jobs_when_details_are_refused() -> None:
    base = "https://cs.test/commandcenter/api"
    info = respx.get(f"{base}/CommServ")
    info.respond(json={"hostName": "cs01", "csVersionInfo": "11 SP46"})
    respx.post(f"{base}/Jobs").respond(json=JOBS)
    with CommvaultClient(base, access_token="t") as cv:
        assert describe_login(cv, saas=False) == "CommServe cs01 version 11 SP46"
        info.respond(403)
        assert describe_login(cv, saas=False) == "334 jobs visible in the last 24 hours"
