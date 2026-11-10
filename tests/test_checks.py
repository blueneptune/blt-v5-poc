"""The CommCell and Rubrik checks (blt.flows.checks), with fakes for the
products: one line per read, and a failed run only when signing in
fails."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from sdk_primer import AuthenticationError, SDKError

from blt.api.admin import CHECKS
from blt.flows import checks, flows


@pytest.fixture(autouse=True)
def plain_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flows, "get_run_logger", lambda: logging.getLogger("test"))


@pytest.fixture
def lines(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    fake = SimpleNamespace(
        info=lambda text, *a: seen.append(text.format(*a)),
        warning=lambda text, *a: seen.append(text.format(*a)),
    )
    monkeypatch.setattr(checks, "logger", fake)
    return seen


class FakeCommvault:
    def __init__(self, *, refuse: bool = False, storage_403: bool = False) -> None:
        self.refuse = refuse
        self.storage_403 = storage_403
        self.jobs = SimpleNamespace(sample=lambda seconds, limit: (12, []))

    def __enter__(self) -> FakeCommvault:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def commvault(monkeypatch: pytest.MonkeyPatch, fake: FakeCommvault, saas: bool = False) -> None:
    settings = SimpleNamespace(
        cv_deployment="saas" if saas else "software", cv_base_url="https://cs01/commandcenter/api"
    )
    monkeypatch.setattr(checks, "settings_from_blocks", lambda name: (settings, name))
    monkeypatch.setattr(checks, "BlockTokenStore", lambda name: name)
    monkeypatch.setattr(checks, "build_client", lambda *a: fake)

    def login(cv: FakeCommvault, *, saas: bool) -> str:
        if cv.refuse:
            raise AuthenticationError("401")
        return "CommServe cs01 version 11.36"

    def storage(cv: FakeCommvault, problems: list[str]) -> list[Any]:
        if cv.storage_403:
            raise SDKError("forbidden", status_code=403)
        return [object(), object()]

    monkeypatch.setattr(checks, "describe_login", login)
    monkeypatch.setattr(checks, "commvault_storage", storage)


def test_a_commcell_that_signs_in_reports_each_read(
    monkeypatch: pytest.MonkeyPatch, lines: list[str]
) -> None:
    commvault(monkeypatch, FakeCommvault())
    assert checks.commvault_check.fn("cv-01") == "3 of 3 checks passed"
    assert "OK   sign in: CommServe cs01 version 11.36" in lines
    assert "OK   jobs: readable (12 in the last 24 hours)" in lines
    assert "OK   storage (pools, libraries): 2 storage targets" in lines


def test_a_read_the_login_may_not_make_is_a_fail_line_not_a_failed_run(
    monkeypatch: pytest.MonkeyPatch, lines: list[str]
) -> None:
    commvault(monkeypatch, FakeCommvault(storage_403=True))
    assert checks.commvault_check.fn("cv-01") == "2 of 3 checks passed"
    assert any(line.startswith("FAIL storage") and "403" in line for line in lines)


def test_a_commcell_that_refuses_the_login_fails_the_run_saying_why(
    monkeypatch: pytest.MonkeyPatch, lines: list[str]
) -> None:
    commvault(monkeypatch, FakeCommvault(refuse=True))
    with pytest.raises(RuntimeError, match="Could not sign in to cv-01: the credentials"):
        checks.commvault_check.fn("cv-01")
    assert len([line for line in lines if line.startswith(("OK", "FAIL"))]) == 1


class FakeRubrik:
    refuse = False

    def __init__(self, account: Any, verify_tls: bool) -> None:
        self.jobs = SimpleNamespace(sample=self._sample)
        self.inventory = SimpleNamespace(sample=lambda n: [1, 2, 3])
        self.sla_domains = SimpleNamespace(list=lambda first: SimpleNamespace(items=[1, 2]))

    def _sample(self, limit: int) -> list[int]:
        if FakeRubrik.refuse:
            raise AuthenticationError("401")
        return [1]

    def __enter__(self) -> FakeRubrik:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def rubrik(monkeypatch: pytest.MonkeyPatch) -> None:
    from blt.flows.rubrik import RubrikAccountBlock

    block = RubrikAccountBlock(
        client_id="client|x",
        client_secret="s",  # type: ignore[arg-type]
        access_token_uri="https://acme.my.rubrik.com/api/client_token",
        service_account_name="blt-reader",
    )
    monkeypatch.setattr(RubrikAccountBlock, "load", classmethod(lambda cls, name: block))
    monkeypatch.setattr(checks, "RubrikClient", FakeRubrik)
    FakeRubrik.refuse = False


def test_a_rubrik_account_that_signs_in_reports_each_read(rubrik: None, lines: list[str]) -> None:
    assert checks.rubrik_check.fn("rsc-acme") == "3 of 3 checks passed"
    assert any("acme.my.rubrik.com as blt-reader" in line for line in lines)
    assert "OK   SLA domains: readable (2 SLA domains in a first page)" in lines


def test_a_rubrik_account_that_refuses_the_service_account_fails_the_run(
    rubrik: None, lines: list[str]
) -> None:
    FakeRubrik.refuse = True
    with pytest.raises(RuntimeError, match="service account was refused"):
        checks.rubrik_check.fn("rsc-acme")


def test_the_api_starts_each_check_by_its_real_name() -> None:
    deployments = yaml.safe_load(Path("prefect.yaml").read_text())["deployments"]
    by_entry = {d["entrypoint"]: d["name"] for d in deployments}
    for kind, flow in (("commvault", checks.commvault_check), ("rubrik", checks.rubrik_check)):
        (flow_name, deployment), _ = CHECKS[kind]
        assert flow.name == flow_name
        assert by_entry[f"blt.flows.checks:{flow.fn.__name__}"] == deployment
