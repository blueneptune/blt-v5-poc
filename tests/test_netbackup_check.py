"""Checking a NetBackup primary and fetching its CA certificate
(blt.netbackup.check), against stand-ins for the SDK and the server."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from netbackup_sdk import NetBackupClient
from sdk_primer import AuthenticationError, ClientError, NotFoundError

from blt.netbackup import check as check_module
from blt.netbackup.check import (
    answers,
    check_primary,
    fallback_url,
    fetch_ca_answering,
    fetch_ca_certificates,
    fingerprint,
)

# Shaped like a certificate; the fingerprint only needs its base64 to decode.
CERT = """-----BEGIN CERTIFICATE-----
MIIBszCCAVmgAwIBAgIUQ0/HjtQhq0CtIR0mHzP4YAm7F/kwCgYIKoZIzj0EAwIw
HzEdMBsGA1UEAwwUYmx0LXRlc3RzIE5ldEJhY2t1cCBDQTAeFw0yNjExMDUwMDAw
MDBaFw0zNjExMDIwMDAwMDBaMB8xHTAbBgNVBAMMFGJsdC10ZXN0cyBOZXRCYWNr
dXAgQ0EwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQ9qZ9p1hU7dnr2u1dcI0e1
rR3tq4nOF2r9V7m2v5t2a2l0f7o3nqO0Qp0z0bJ8kQ3w3m5xkQ3w3m5xkQ3w3m5x
o1MwUTAdBgNVHQ4EFgQUAAAAAAAAAAAAAAAAAAAAAAAAAAAwHwYDVR0jBBgwFoAU
AAAAAAAAAAAAAAAAAAAAAAAAAAAwDwYDVR0TAQH/BAUwAwEB/zAKBggqhkjOPQQD
AgNIADBFAiEA0000000000000000000000000000000000000000000CIBAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
-----END CERTIFICATE-----"""


def _raise(error: Exception) -> Any:
    def call(*_: Any, **__: Any) -> Any:
        raise error

    return call


def _nbu(**overrides: Any) -> NetBackupClient:
    fake = SimpleNamespace(
        api_version="12.0",
        server=SimpleNamespace(
            info=lambda: {"serverName": "nbu01", "nbuVersion": "NetBackup_10.5.0.1"}
        ),
        jobs=SimpleNamespace(
            sample=lambda limit=5, updated_after=None: (42 if updated_after is None else 7, [])
        ),
        raw=SimpleNamespace(
            catalog=SimpleNamespace(list_slp_images=lambda page_limit=None: {"data": []}),
            config=SimpleNamespace(
                list_slps=lambda page_limit, page_offset: {
                    "data": [
                        {
                            "id": "slp-gold",
                            "attributes": {
                                "slpName": "slp-gold",
                                "operationList": [
                                    {"operationIndex": 1, "operationType": "BACKUP"},
                                    {
                                        "operationIndex": 2,
                                        "operationType": "DUPLICATION",
                                        "sourceOperationIndex": 1,
                                    },
                                ],
                            },
                        }
                    ]
                }
            ),
            storage=SimpleNamespace(
                list_storage_units=lambda page_limit, page_offset: {"data": []},
                list_disk_pools=lambda page_limit, page_offset: {
                    "data": [
                        {
                            "id": "PureDisk:dp1",
                            "attributes": {"name": "dp1", "storageCategory": "MSDP"},
                        }
                    ]
                },
            ),
        ),
    )
    for path, value in overrides.items():
        target = fake
        *parents, name = path.split(".")
        for parent in parents:
            target = getattr(target, parent)
        setattr(target, name, value)
    return cast(NetBackupClient, fake)


def test_every_read_is_tried_and_said() -> None:
    lines = [c.line() for c in check_primary(_nbu())]
    assert lines == [
        "OK   sign in and server: nbu01, NetBackup 10.5.0.1, API version 12.0",
        "OK   storage (disk pools, storage units): 1 storage targets (MSDP)",
        "OK   SLPs (to trace copies on the Storage page): 1 SLPs, 2 steps",
        "OK   SLP images (for the SLP backlog): readable (none waiting or kept just now)",
        "OK   jobs: readable (42 jobs); 7 changed in the last 24 h",
    ]


def test_a_refused_key_stops_at_the_first_check() -> None:
    (only,) = check_primary(
        _nbu(**{"server.info": _raise(AuthenticationError("401", status_code=401))})
    )
    assert not only.ok and "API key was refused" in only.detail


def test_a_missing_permission_or_endpoint_is_one_failed_line() -> None:
    checks = check_primary(
        _nbu(
            **{
                "raw.catalog.list_slp_images": _raise(NotFoundError("404", status_code=404)),
                "raw.storage.list_disk_pools": _raise(ClientError("403", status_code=403)),
            }
        )
    )
    assert [c.ok for c in checks] == [True, False, True, True, True]
    assert "read access" in checks[1].detail
    # NetBackup 10.0 has no SLP images listing: said, not failed - nothing collects it yet.
    assert "not on this NetBackup version" in checks[3].detail


def test_a_netbackup_without_a_last_update_filter_is_said_in_the_jobs_line() -> None:
    nbu = _nbu()
    nbu.delta_style = "ended-or-active"  # type: ignore[misc]
    jobs = check_primary(nbu)[-1]
    assert jobs.ok and "takes no lastUpdateTime filter" in jobs.detail


def test_ca_certificates_come_with_fingerprints(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("cryptography")
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.headers["accept"])
        if "13.0" in request.headers["accept"]:
            return httpx.Response(406)
        return httpx.Response(200, json={"webRootCert": [CERT], "cacert": [CERT + "\n"]})

    real = httpx.Client

    def client(**kwargs: Any) -> httpx.Client:
        assert kwargs["verify"] is False
        return real(transport=httpx.MockTransport(answer), timeout=kwargs["timeout"])

    monkeypatch.setattr(check_module.httpx, "Client", client)
    (found,) = fetch_ca_certificates("https://nbu01:1556/netbackup")
    assert found.pem.startswith("-----BEGIN CERTIFICATE-----") and found.pem.endswith("-----\n")
    assert found.sha256 == fingerprint(CERT)
    assert len(found.sha256) == 95  # 32 bytes, as AA:BB:...
    # the newest version first, then the next on a 406
    assert ["13.0" in a for a in asked] == [True, False]


def test_the_api_starts_these_flows_by_their_real_names() -> None:
    from pathlib import Path

    import yaml

    from blt.api.admin import NETBACKUP_CA, NETBACKUP_CHECK
    from blt.flows.netbackup import netbackup_check, netbackup_fetch_ca

    deployments = yaml.safe_load(Path("prefect.yaml").read_text())["deployments"]
    for flow, (flow_name, deployment) in (
        (netbackup_check, NETBACKUP_CHECK),
        (netbackup_fetch_ca, NETBACKUP_CA),
    ):
        assert flow.name == flow_name
        entry = f"blt.flows.netbackup:{flow.fn.__name__}"
        assert [d["name"] for d in deployments if d["entrypoint"] == entry] == [deployment]


@pytest.mark.parametrize(
    ("url", "fallback"),
    [
        ("https://nbu01:1556/netbackup", "https://nbu01:443/netbackup"),
        ("https://[fd00::5]:1556/netbackup", "https://[fd00::5]:443/netbackup"),
        ("https://nbu01:443/netbackup", None),  # already there
        ("https://nbu01:8443/netbackup", None),  # a port chosen on purpose
    ],
)
def test_only_the_default_port_falls_back_to_443(url: str, fallback: str | None) -> None:
    assert fallback_url(url) == fallback


def _answer_on(monkeypatch: pytest.MonkeyPatch, ports: set[int]) -> list[str]:
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        # httpx leaves an https address's default port, 443, out of its URL.
        if (request.url.port or 443) not in ports:
            raise httpx.ConnectTimeout("timed out", request=request)
        return httpx.Response(200, json={"webRootCert": [CERT]})

    real = httpx.Client

    def client(**kwargs: Any) -> httpx.Client:
        return real(transport=httpx.MockTransport(answer), timeout=kwargs["timeout"])

    monkeypatch.setattr(check_module.httpx, "Client", client)
    return asked


def test_a_primary_that_answers_only_on_443_is_found_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _answer_on(monkeypatch, {443})
    url, (found,), why = fetch_ca_answering("https://nbu01:1556/netbackup")
    assert url == "https://nbu01:443/netbackup" and why == "timed out"
    assert found.sha256 == fingerprint(CERT)
    assert asked[0].startswith("https://nbu01:1556/") and asked[-1].startswith("https://nbu01/")
    assert answers("https://nbu01:443/netbackup") and not answers("https://nbu01:1556/netbackup")


def test_the_default_port_is_used_when_it_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _answer_on(monkeypatch, {443, 1556})
    url, _, why = fetch_ca_answering("https://nbu01:1556/netbackup")
    assert (url, why) == ("https://nbu01:1556/netbackup", None)
    assert all(":1556/" in a for a in asked)


def test_neither_port_answering_names_both(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer_on(monkeypatch, set())
    with pytest.raises(RuntimeError, match=r"nbu01:1556.*nbu01:443"):
        fetch_ca_answering("https://nbu01:1556/netbackup")
    # A port chosen on purpose is not second-guessed.
    with pytest.raises(httpx.ConnectTimeout):
        fetch_ca_answering("https://nbu01:8443/netbackup")
