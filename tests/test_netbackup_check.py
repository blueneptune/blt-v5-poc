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
from blt.netbackup.check import check_primary, fetch_ca_certificates, fingerprint

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
        jobs=SimpleNamespace(sample=lambda limit=5: (42, [])),
        raw=SimpleNamespace(
            catalog=SimpleNamespace(list_slp_images=lambda page_limit=None: {"data": []}),
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
        "OK   SLP images (for the SLP backlog): readable (none waiting or kept just now)",
        "OK   jobs: readable (42 jobs)",
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
    assert [c.ok for c in checks] == [True, False, False, True]
    assert "read access" in checks[1].detail
    assert "does not have it" in checks[2].detail


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
