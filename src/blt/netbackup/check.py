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

import httpx
from netbackup_sdk import NetBackupClient
from netbackup_sdk.client import tls_context
from netbackup_sdk.versions import AUTO_ORDER, media_type
from sdk_primer import AuthenticationError, NotFoundError, SDKError

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


def verifies(url: str, pem: str, timeout: float = 20.0) -> str | None:
    """None if the primary's TLS certificate verifies against `pem` for
    this address; otherwise why not."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "ca.pem"
        path.write_text(pem)
        try:
            with httpx.Client(verify=tls_context(path), timeout=timeout) as http:
                http.get(f"{url}/ping", headers={"Accept": "*/*"})
        except httpx.ConnectError as error:
            return str(error)
    return None
