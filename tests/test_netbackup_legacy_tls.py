"""A primary whose NetBackup CA has a 1024-bit key (one made in 2017 and
kept through every upgrade since): refused by today's TLS defaults,
accepted with legacy key sizes - and still checked for chain and host name.

Against a real TLS server on 127.0.0.1, with certificates made here."""

from __future__ import annotations

import datetime as dt
import http.server
import ipaddress
import ssl
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("cryptography")
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from blt.netbackup.check import legacy_context, verifies, weak_key  # noqa: E402


def _cert(
    subject: str, key: rsa.RSAPrivateKey, issuer: tuple[str, rsa.RSAPrivateKey] | None, **extra: Any
) -> x509.Certificate:
    now = dt.datetime.now(dt.UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
    issuer_name = (
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer[0])]) if issuer else name
    )
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
    )
    if issuer is None:
        builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
    else:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(extra["host"]), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                if extra["host"] == "localhost"
                else [x509.DNSName(extra["host"])]
            ),
            False,
        )
    return builder.sign(issuer[1] if issuer else key, hashes.SHA256())


def _pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture(scope="module")
def ca() -> tuple[str, rsa.RSAPrivateKey]:
    return "nbatd", rsa.generate_private_key(public_exponent=65537, key_size=1024)


def _serve(tmp: Path, ca: tuple[str, rsa.RSAPrivateKey], host: str) -> Iterator[str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = _cert(host, key, ca, host=host)
    (tmp / "leaf.pem").write_text(_pem(leaf))
    (tmp / "leaf.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )

    class Ping(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ping)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.set_ciphers("DEFAULT:@SECLEVEL=0")  # the server side may serve anything
    context.load_cert_chain(tmp / "leaf.pem", tmp / "leaf.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_address[1]}/netbackup"
    finally:
        server.shutdown()


@pytest.fixture
def primary(tmp_path: Path, ca: tuple[str, rsa.RSAPrivateKey]) -> Iterator[str]:
    yield from _serve(tmp_path, ca, "localhost")


def test_a_1024_bit_ca_verifies_only_with_legacy_key_sizes(
    primary: str, ca: tuple[str, rsa.RSAPrivateKey]
) -> None:
    ca_pem = _pem(_cert(ca[0], ca[1], None))
    problem = verifies(primary, ca_pem)
    if problem is None:
        pytest.skip("this OpenSSL accepts 1024-bit keys at its default level")
    assert weak_key(problem), problem
    assert verifies(primary, ca_pem, legacy=True) is None


def test_legacy_key_sizes_still_check_the_ca(
    primary: str, ca: tuple[str, rsa.RSAPrivateKey]
) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    stranger = _pem(_cert("someone-else", other, None))
    problem = verifies(primary, stranger, legacy=True)
    assert problem and not weak_key(problem)


def test_legacy_key_sizes_still_check_the_host_name(
    tmp_path: Path, ca: tuple[str, rsa.RSAPrivateKey]
) -> None:
    ca_pem = _pem(_cert(ca[0], ca[1], None))
    for url in _serve(tmp_path, ca, "nbu-elsewhere.example"):
        problem = verifies(url, ca_pem, legacy=True)
        assert problem and "hostname" in problem.lower(), problem


def test_the_block_uses_legacy_tls_only_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    from blt.flows import netbackup as flows

    seen: list[dict[str, Any]] = []

    class Client:
        def __init__(self, url: str, **kwargs: Any) -> None:
            seen.append(kwargs)

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    monkeypatch.setattr(flows, "NetBackupClient", Client)
    ca_pem = _pem(
        _cert("nbatd", rsa.generate_private_key(public_exponent=65537, key_size=1024), None)
    )
    for legacy in (False, True):
        block = flows.NetBackupPrimaryBlock(
            url="https://nbu01:443/netbackup",
            api_key="k",  # type: ignore[arg-type]
            ca_certificate=ca_pem,
            legacy_keys=legacy,
        )
        with block.client():
            pass
    assert seen[0]["transport"] is None
    assert isinstance(seen[1]["transport"], httpx.HTTPTransport)
    assert seen[1]["ca_bundle"] is not None and seen[1]["verify_tls"] is True


def test_legacy_context_keeps_verification_on() -> None:
    context = legacy_context(None)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
