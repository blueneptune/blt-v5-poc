"""Access-token renewal: when it happens, and that the new pair reaches
the .env file intact."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from sdk_primer import AuthenticationError, ServerError

from blt.collector.settings import load_settings
from blt.collector.tokens import env_token_saver, update_env_file
from blt.commvault.auth import TokenSet
from blt.commvault.client import CommvaultClient

BASE = "https://cs.test/commandcenter/api"
RENEWED = {
    "accessToken": "new-access",
    "refreshToken": "new-refresh",
    "tokenExpiryTimestamp": 1_800_000_000,
    "renewableUntilTimestamp": 1_805_000_000,
}


def _commserv_by_token() -> respx.Route:
    """A CommServe that only accepts the renewed token."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authtoken") == "Bearer new-access":
            return httpx.Response(200, json={"hostName": "CS"})
        return httpx.Response(401, json={"errorMessage": "Access denied", "errorCode": 5})

    return respx.get(f"{BASE}/CommServ").mock(side_effect=handler)


@respx.mock
def test_a_401_renews_saves_and_retries() -> None:
    commserv = _commserv_by_token()
    renew = respx.post(f"{BASE}/V4/AccessToken/Renew").respond(json=RENEWED)
    saved: list[TokenSet] = []

    tokens = TokenSet("old-access", "old-refresh")
    with CommvaultClient(
        BASE, access_token=tokens, on_token_renew=lambda t, raw: saved.append(t)
    ) as client:
        assert client.commserve_info() == {"hostName": "CS"}
        client.commserve_info()

    # One renewal, carrying the old pair; later calls reuse the new token.
    assert renew.call_count == 1
    assert json.loads(renew.calls[0].request.content) == {
        "accessToken": "old-access",
        "refreshToken": "old-refresh",
    }
    assert commserv.call_count == 3  # 401, retry, second call
    assert saved == [
        TokenSet(
            "new-access",
            "new-refresh",
            datetime.fromtimestamp(1_800_000_000, tz=UTC),
            datetime.fromtimestamp(1_805_000_000, tz=UTC),
        )
    ]


@respx.mock
def test_a_token_known_to_be_expiring_is_renewed_before_it_is_used() -> None:
    commserv = _commserv_by_token()
    respx.post(f"{BASE}/V4/AccessToken/Renew").respond(json=RENEWED)

    tokens = TokenSet(
        "old-access", "old-refresh", expires_at=datetime.now(UTC) + timedelta(minutes=1)
    )
    with CommvaultClient(BASE, access_token=tokens) as client:
        client.commserve_info()

    assert commserv.call_count == 1  # never sent the dying token


@respx.mock
def test_renewal_falls_back_to_no_header_when_the_old_token_is_rejected() -> None:
    _commserv_by_token()
    renew = respx.post(f"{BASE}/V4/AccessToken/Renew").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json=RENEWED)]
    )

    with CommvaultClient(BASE, access_token=TokenSet("old-access", "old-refresh")) as client:
        client.commserve_info()

    assert "Authtoken" in renew.calls[0].request.headers
    assert "Authtoken" not in renew.calls[1].request.headers


@respx.mock
def test_refused_renewal_says_to_create_a_new_token() -> None:
    _commserv_by_token()
    respx.post(f"{BASE}/V4/AccessToken/Renew").respond(
        json={"errorCode": 7, "errorMessage": "Refresh token is invalid"}
    )
    tokens = TokenSet("old-access", "old-refresh", renewable_until=datetime(2020, 1, 1, tzinfo=UTC))

    with (
        CommvaultClient(BASE, access_token=tokens) as client,
        pytest.raises(AuthenticationError, match="Refresh token is invalid.*2020-01-01"),
    ):
        client.commserve_info()


@respx.mock
def test_without_a_refresh_token_a_401_is_just_a_failure() -> None:
    _commserv_by_token()
    renew = respx.post(f"{BASE}/V4/AccessToken/Renew").respond(json=RENEWED)

    with (
        CommvaultClient(BASE, access_token="old-access") as client,
        pytest.raises(AuthenticationError),
    ):
        client.commserve_info()
    assert renew.call_count == 0


@respx.mock
def test_a_maintenance_page_is_an_error_not_an_empty_result() -> None:
    respx.post(f"{BASE}/Jobs").respond(
        200, html="<html><title>Scheduled Maintenance - Your Data is Safe</title></html>"
    )
    with (
        CommvaultClient(BASE, access_token="tok") as client,
        pytest.raises(ServerError, match="maintenance"),
    ):
        list(client.iter_job_pages(lookup_seconds=60))


def test_renewed_tokens_are_written_back_to_the_env_file(tmp_path: Path) -> None:
    env = tmp_path / "prod.env"
    env.write_text(
        "# my commcell\n"
        "CV_BASE_URL=https://cs.test/api\n"
        "CV_ACCESS_TOKEN=old-access\n"
        "# CV_REFRESH_TOKEN=commented-out\n"
        "CV_REFRESH_TOKEN=old-refresh\n"
        "CV_PAGE_SIZE=250\n"
    )
    (tmp_path / "blt.env").write_text("BLT_API_KEY=k\n")
    new = TokenSet(
        "new-access",
        "new-refresh",
        datetime(2026, 10, 6, 3, 0, tzinfo=UTC),
        datetime(2026, 12, 4, 7, 59, 59, tzinfo=UTC),
    )

    env_token_saver(env)(new, RENEWED)

    assert env.read_text() == (
        "# my commcell\n"
        "CV_BASE_URL=https://cs.test/api\n"
        "CV_ACCESS_TOKEN=new-access\n"
        "# CV_REFRESH_TOKEN=commented-out\n"
        "CV_REFRESH_TOKEN=new-refresh\n"
        "CV_PAGE_SIZE=250\n"
        "CV_TOKEN_EXPIRES_AT=2026-10-06T03:00:00Z\n"
        "CV_TOKEN_RENEWABLE_UNTIL=2026-12-04T07:59:59Z\n"
    )
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    # The CommServe's answer is kept verbatim as a safety net.
    assert json.loads((tmp_path / ".prod.renewed.json").read_text()) == RENEWED

    # And the next run reads back exactly what was saved.
    settings = load_settings("prod", tmp_path)
    assert settings.cv_access_token.get_secret_value() == "new-access"  # type: ignore[union-attr]
    assert settings.cv_refresh_token.get_secret_value() == "new-refresh"  # type: ignore[union-attr]
    assert settings.cv_token_expires_at == new.expires_at
    assert settings.cv_token_renewable_until == new.renewable_until


def test_blank_values_in_the_env_file_mean_unset(tmp_path: Path) -> None:
    env = tmp_path / "prod.env"
    env.write_text("CV_BASE_URL=https://cs.test/api\nCV_ACCESS_TOKEN=t\n")
    (tmp_path / "blt.env").write_text("BLT_API_KEY=k\n")
    update_env_file(env, {"CV_TOKEN_EXPIRES_AT": "", "CV_USERNAME": ""})

    settings = load_settings("prod", tmp_path)
    assert settings.cv_token_expires_at is None
    assert settings.cv_username is None
