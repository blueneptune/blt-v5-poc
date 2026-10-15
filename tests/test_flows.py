"""blt on Prefect: CommCell blocks, the block-backed token store, and
the parent flows' handling of several CommCells."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from commvault_sdk import TokenSet
from prefect.testing.utilities import prefect_test_harness
from pydantic import SecretStr, ValidationError

from blt.flows import flows
from blt.flows.blocks import BlockTokenStore, CommvaultCommCell, settings_from_blocks
from blt.flows.importer import blocks_from_env, describe, env_names
from blt.flows.manage import apply_settings, credentials_from_env, summary

MSP_ENV = """\
CV_BASE_URL=https://api.metallic.io
CV_DEPLOYMENT=saas
CV_ACCESS_TOKEN=msp-access-secret
CV_REFRESH_TOKEN=msp-refresh-secret
"""
TENANT_ENV = """\
# a customer
CV_CREDENTIALS=msp
CV_ACCOUNT_ID=1001
CV_PAGE_SIZE=1000
CV_USERNAME=
"""
SOFTWARE_ENV = """\
CV_BASE_URL=https://cs01/commandcenter/api
CV_ACCESS_TOKEN=cs-access-secret
CV_VERIFY_TLS=false
"""


@pytest.fixture(scope="module")
def prefect_server() -> Iterator[None]:
    # A throwaway Prefect server with its own SQLite database.
    with prefect_test_harness():
        yield


@pytest.fixture
def config(tmp_path: Path) -> Path:
    (tmp_path / "blt.env").write_text("BLT_API_KEY=k\n")
    (tmp_path / "msp.env").write_text(MSP_ENV)
    (tmp_path / "acme.env").write_text(TENANT_ENV)
    (tmp_path / "cs01.env").write_text(SOFTWARE_ENV)
    return tmp_path


def test_env_files_become_blocks_and_the_msp_login_is_not_collected(config: Path) -> None:
    names = env_names(config)
    assert names == ["acme", "cs01", "msp"]  # blt.env is not a CommCell
    blocks = blocks_from_env(config, names)

    assert (blocks["msp"].collect, blocks["acme"].collect, blocks["cs01"].collect) == (
        False,
        True,
        True,
    )
    acme = blocks["acme"]
    assert (acme.credentials, acme.account_id, acme.username) == ("msp", "1001", None)
    assert acme.options == {"CV_PAGE_SIZE": "1000"}
    assert blocks["cs01"].verify_tls is False
    # Round trip: the block gives back what the file said.
    assert blocks["cs01"].to_env() == {
        "CV_BASE_URL": "https://cs01/commandcenter/api",
        "CV_ACCESS_TOKEN": "cs-access-secret",
        "CV_VERIFY_TLS": "false",
    }


def test_what_the_import_prints_never_includes_a_secret(config: Path) -> None:
    for name, block in blocks_from_env(config, env_names(config)).items():
        line = describe(name, block)
        assert "secret" not in line.replace("secrets:", "")
    assert "access_token, refresh_token" in describe("msp", blocks_from_env(config, ["msp"])["msp"])


def test_settings_layer_the_credentials_block_under_the_commcells_own(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BLT_API_KEY", "from-the-worker")
    blocks = blocks_from_env(config, env_names(config))
    settings, token_block = settings_from_blocks("acme", load=blocks.__getitem__)

    assert token_block == "msp"  # renewals go to the shared login
    assert settings.cv_base_url == "https://api.metallic.io"
    assert settings.cv_deployment == "saas"
    assert settings.tenant_headers == {"lhAccountId": "1001"}
    assert settings.cv_page_size == 1000
    assert settings.cv_access_token is not None
    assert settings.cv_access_token.get_secret_value() == "msp-access-secret"
    assert settings.blt_api_key.get_secret_value() == "from-the-worker"


def test_a_renewal_is_saved_to_the_block_and_read_back(prefect_server: None) -> None:
    CommvaultCommCell(
        base_url="https://cs01/commandcenter/api", access_token=SecretStr("old")
    ).save("renew-test", overwrite=True)
    store = BlockTokenStore("renew-test")
    renewed = TokenSet(
        access_token="new-access",
        refresh_token="new-refresh",
        expires_at=datetime(2026, 10, 16, 12, tzinfo=UTC),
        renewable_until=datetime(2026, 12, 1, tzinfo=UTC),
    )
    with store.lock():
        store.save(renewed, {"accessToken": "new-access"})

    assert store.load() == renewed
    block = CommvaultCommCell.load("renew-test")
    assert block.base_url == "https://cs01/commandcenter/api"  # the rest is untouched
    assert block.last_renewal_response is not None
    assert "new-access" in block.last_renewal_response.get_secret_value()


def test_only_blocks_marked_for_collection_are_collected(prefect_server: None) -> None:
    CommvaultCommCell(collect=False).save("lister-msp", overwrite=True)
    CommvaultCommCell(credentials="lister-msp", account_id="1").save("lister-acme", overwrite=True)
    found = flows.collectable_commcells()
    assert "lister-acme" in found and "lister-msp" not in found


def test_one_commcell_failing_does_not_stop_the_others(
    prefect_server: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(commcell: str, kind: str, work: object) -> str:
        if commcell == "bad":
            raise RuntimeError("CommServe unreachable")
        return f"{kind} done"

    monkeypatch.setattr(flows, "_with_commcell", fake)
    # The real child retries after five minutes; not here.
    monkeypatch.setattr(flows, "collect_commcell", flows.collect_commcell.with_options(retries=0))
    state = flows.collect_jobs(["good", "bad", "also-good"], return_state=True)

    assert state.is_failed()
    message = str(state.message) + str(state.result(raise_on_failure=False))
    assert "bad" in message and "good" in message
    good_runs = flows.collect_jobs(["good", "also-good"])
    assert good_runs == {"good": "collection done", "also-good": "collection done"}


def test_set_changes_only_what_is_named_and_refuses_secrets() -> None:
    block = CommvaultCommCell(
        base_url="https://short/commandcenter/api", access_token=SecretStr("keep-me")
    )
    changed = apply_settings(
        block, ["base_url=https://short.corp.example.com/commandcenter/api", "CV_PAGE_SIZE=1000"]
    )
    assert changed == ["base_url", "CV_PAGE_SIZE"]
    assert block.base_url == "https://short.corp.example.com/commandcenter/api"
    assert block.options == {"CV_PAGE_SIZE": "1000"}
    assert block.access_token is not None
    assert block.access_token.get_secret_value() == "keep-me"
    with pytest.raises(ValueError, match="tokens-from-env"):
        apply_settings(block, ["access_token=typed-on-a-command-line"])
    with pytest.raises(ValidationError):
        apply_settings(block, ["deployment=sass"])


def test_tokens_from_env_restores_credentials_and_leaves_the_rest(config: Path) -> None:
    # As after a UI save that wiped the tokens but kept an edited URL.
    block = CommvaultCommCell(base_url="https://cs01.corp.example.com/commandcenter/api")
    copied = credentials_from_env(block, config / "cs01.env")
    assert copied == ["access_token"]
    assert block.access_token is not None
    assert block.access_token.get_secret_value() == "cs-access-secret"
    assert block.base_url == "https://cs01.corp.example.com/commandcenter/api"
    assert "secret" not in summary("cs01", block)
