"""What the admin pages ask of a NetBackup primary, run on a worker
(blt.flows.netbackup): is it reachable and does blt's key work, and what
is its CA certificate.

- `check_primary(nbu)` - each read blt makes, once: who the server is,
  storage, SLP images (for the SLP backlog), jobs. Each gives a line
  saying what was found, or why it failed - most often a key whose user
  lacks a permission, or an endpoint the NetBackup version does not have.
- `fetch_ca_certificates(url)` - GET /security/cacert, which NetBackup
  answers without signing in (it is how `nbcertcmd -getCACertificate`
  starts). Fetched without verifying TLS - there is nothing to verify
  against yet - so each certificate comes with its SHA-256 fingerprint,
  to be compared with the primary's own before it is trusted, and a test
  of whether it actually verifies the primary's TLS certificate.
- `legacy_context(ca)` - TLS that still checks the chain, expiry and host
  name but accepts keys today's defaults call too weak (1024-bit RSA): for
  a primary whose NetBackup CA predates 2048-bit keys (a 2017 CA, kept
  through every upgrade since). Each primary opts in (`legacy_keys`).
- `fetch_ca_answering(url)` - the same, trying port 443 when the default
  1556 does not answer: some NetBackup 10.5 primaries serve the API only
  through their web server, on 443.
"""

from __future__ import annotations

import hashlib
import re
import ssl
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from netbackup_sdk import NetBackupClient
from netbackup_sdk.client import tls_context
from netbackup_sdk.versions import AUTO_ORDER, media_type
from sdk_primer import AuthenticationError, NotFoundError, SDKError

# OpenSSL's words when a certificate's key is below its security level:
# "certificate key too weak", "CA key too small", "EE key too small".
WEAK_KEY = re.compile(r"key too (weak|small)", re.IGNORECASE)

DEFAULT_PORT = 1556
FALLBACK_PORT = 443

PEM = re.compile(r"-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str

    def line(self) -> str:
        return f"{'OK  ' if self.ok else 'FAIL'} {self.name}: {self.detail}"


def _why(error: Exception) -> str:
    if isinstance(error, AuthenticationError):
        return "the API key was refused (401) - is it right, and not expired?"
    if isinstance(error, SDKError) and error.status_code == 403:
        return "the key's user may not read this (403) - give its role read access here"
    if isinstance(error, NotFoundError):
        return "this NetBackup version does not have it (404)"
    return str(error)


def _attempt(name: str, call: Callable[[], str]) -> Check:
    try:
        return Check(name, True, call())
    except Exception as error:  # noqa: BLE001 - each check reports, never stops the rest
        return Check(name, False, _why(error))


def check_primary(nbu: NetBackupClient) -> list[Check]:
    from blt.storage.netbackup import netbackup_storage

    def server() -> str:
        info = nbu.server.info()
        version = str(info.get("nbuVersion") or "?").removeprefix("NetBackup_")
        nbu.jobs.sample(1)  # settles the API version
        return f"{info.get('serverName', '?')}, NetBackup {version}, API version {nbu.api_version}"

    def storage() -> str:
        targets = netbackup_storage(nbu)
        kinds = sorted({t.product_type or t.kind for t in targets})
        return f"{len(targets)} storage targets ({', '.join(kinds) or 'none'})"

    def slp_images() -> str:
        answer = nbu.raw.catalog.list_slp_images(page_limit=1) or {}
        found = len(answer.get("data") or [])
        return "readable" + ("" if found else " (none waiting or kept just now)")

    def jobs() -> str:
        total, recent = nbu.jobs.sample(1)
        return f"readable ({total if total is not None else len(recent)} jobs)"

    first = _attempt("sign in and server", server)
    if not first.ok:
        return [first]
    return [
        first,
        _attempt("storage (disk pools, storage units)", storage),
        _attempt("SLP images (for the SLP backlog)", slp_images),
        _attempt("jobs", jobs),
    ]


@dataclass(frozen=True)
class CaCertificate:
    pem: str
    sha256: str
    subject: str | None


def fingerprint(pem: str) -> str:
    digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def _subject(pem: str) -> str | None:
    try:
        from cryptography import x509

        return x509.load_pem_x509_certificate(pem.encode()).subject.rfc4514_string()
    except Exception:  # noqa: BLE001 - only a label
        return None


def _pems(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        for match in PEM.finditer(value):
            yield match.group(0).strip() + "\n"
    elif isinstance(value, dict):
        for key in sorted(value, key=lambda k: k != "webRootCert"):
            yield from _pems(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _pems(item)


def fetch_ca_certificates(url: str, timeout: float = 20.0) -> list[CaCertificate]:
    """The CA certificates the primary hands out, the web service's
    first. Unverified: see the module docstring."""
    with httpx.Client(verify=False, timeout=timeout) as http:  # noqa: S501 - see above
        for version in AUTO_ORDER:
            response = http.get(f"{url}/security/cacert", headers={"Accept": media_type(version)})
            if response.status_code != 406:
                break
        response.raise_for_status()
    seen: dict[str, CaCertificate] = {}
    for pem in _pems(response.json()):
        print_ = fingerprint(pem)
        seen.setdefault(print_, CaCertificate(pem, print_, _subject(pem)))
    return list(seen.values())


def fallback_url(url: str) -> str | None:
    """The same primary on port 443, for an address on the default 1556:
    NetBackup 10.5 can serve its API only through its web server. None
    for an address on any other port - one chosen on purpose."""
    parts = urlsplit(url)
    if parts.port != DEFAULT_PORT or not parts.hostname:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return urlunsplit((parts.scheme, f"{host}:{FALLBACK_PORT}", parts.path, "", ""))


def fetch_ca_answering(
    url: str, timeout: float = 20.0
) -> tuple[str, list[CaCertificate], str | None]:
    """fetch_ca_certificates, falling back to port 443 when the default
    port does not answer at all. Gives the address that answered, its
    certificates, and - if that is the fallback - why the first did not."""
    try:
        return url, fetch_ca_certificates(url, timeout), None
    except httpx.TransportError as first:
        other = fallback_url(url)
        if other is None:
            raise
        why = str(first) or type(first).__name__
        try:
            return other, fetch_ca_certificates(other, timeout), why
        except httpx.TransportError as second:
            raise RuntimeError(
                f"Neither answered: {url} ({why}), {other} ({str(second) or type(second).__name__})"
            ) from second


def answers(url: str, timeout: float = 10.0) -> bool:
    """Whether a NetBackup API answers at this address at all (with
    anything: /security/cacert needs no sign-in)."""
    try:
        with httpx.Client(verify=False, timeout=timeout) as http:  # noqa: S501 - only a probe
            http.get(f"{url}/security/cacert")
    except httpx.TransportError:
        return False
    return True


def legacy_context(ca: Path | None) -> ssl.SSLContext:
    """As the SDK's own TLS (chain, expiry and host name all checked), but
    at OpenSSL security level 1, which accepts 1024-bit RSA keys - and
    nothing else changed."""
    context = tls_context(ca) if ca else ssl.create_default_context()
    context.set_ciphers("DEFAULT:@SECLEVEL=1")
    return context


def weak_key(problem: str | None) -> bool:
    """Whether a TLS failure is only a key below today's minimum."""
    return bool(problem and WEAK_KEY.search(problem))


def verifies(url: str, pem: str, timeout: float = 20.0, *, legacy: bool = False) -> str | None:
    """None if the primary's TLS certificate verifies against `pem` for
    this address; otherwise why not. `legacy`: with legacy key sizes
    accepted (legacy_context)."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "ca.pem"
        path.write_text(pem)
        context = legacy_context(path) if legacy else tls_context(path)
        try:
            with httpx.Client(verify=context, timeout=timeout) as http:
                http.get(f"{url}/ping", headers={"Accept": "*/*"})
        except httpx.ConnectError as error:
            return str(error)
    return None
