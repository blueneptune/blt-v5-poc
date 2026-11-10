"""NetBackup on Prefect: the primary block and the storage flows.

One block per NetBackup primary server, of type "blt NetBackup primary",
named as blt stores the primary's data. It holds the address, an API key
(made in the NetBackup web UI for a user with read access to storage),
and the primary's CA certificate when it is not publicly signed. An API
key does not renew, so as for Rubrik there is nothing to write back.

- netbackup_jobs_primary    - one primary's jobs (blt-netbackup-jobs-
                              primary/per-primary).
- netbackup_collect_jobs    - every primary block's jobs, one flow run
                              each, at once (blt-netbackup-collect-jobs/
                              every-15-minutes).
- netbackup_storage_primary - one primary's storage (blt-netbackup-
                              storage-primary/per-primary).
- netbackup_collect_storage - every primary block, one flow run each, at
                              the same time (blt-netbackup-collect-
                              storage/hourly).
- netbackup_check           - one primary's key and reads, for the admin
                              pages (blt-netbackup-check/by-hand).
- netbackup_fetch_ca        - a primary's CA certificate, by address,
                              before it has a block (blt-netbackup-fetch-
                              ca/by-hand).

The API key needs read access to jobs as well as storage
(docs/netbackup.md).
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
from loguru import logger
from netbackup_sdk import NetBackupClient
from prefect import flow, get_run_logger
from prefect.blocks.core import Block
from prefect.client.orchestration import get_client
from prefect.client.schemas.objects import FlowRun
from prefect.concurrency.sync import concurrency
from prefect.deployments import run_deployment
from pydantic import Field, SecretStr

from blt.netbackup.check import (
    answers,
    check_primary,
    fallback_url,
    fetch_ca_answering,
    legacy_context,
    verifies,
    weak_key,
)
from blt.netbackup.collect import run_netbackup_collection
from blt.netbackup.source import NetBackupJobs
from blt.netbackup.store import NetBackupStore
from blt.storage.netbackup import netbackup_storage
from blt.storage.store import StorageStore

from .flows import SLOTS, fan_out, logs_to_prefect, source_queue
from .locks import source_lock
from .rubrik import BltApiSettings

BLOCK_TYPE_SLUG = "blt-netbackup-primary"
STORAGE_ONE = "blt-netbackup-storage-primary/per-primary"
JOBS_ONE = "blt-netbackup-jobs-primary/per-primary"


class NetBackupPrimaryBlock(Block):
    """One NetBackup primary server, reached with an API key."""

    _block_type_name = "blt NetBackup primary"
    _block_type_slug = BLOCK_TYPE_SLUG
    _description = (
        "Where blt reaches one NetBackup primary server, and the API key it uses - made in the "
        "NetBackup web UI for a user that may read storage."
    )

    url: str = Field(description="https://<primary>:1556/netbackup")
    api_key: SecretStr = Field(description="The API key.")
    ca_certificate: str | None = Field(
        default=None,
        description="The primary's CA certificate (PEM), when it is not signed by a public CA.",
    )
    verify_tls: bool = True
    legacy_keys: bool = Field(
        default=False,
        description="Accept TLS keys today's defaults call too weak (1024-bit RSA), for a "
        "primary whose CA predates 2048-bit keys. Chain, expiry and host name are still checked.",
    )
    collect: bool = Field(default=True, description="Collected by the scheduled flows.")
    collector: str | None = Field(
        default=None,
        description="The remote collector that reaches it (its Prefect work queue; "
        "docs/remote-collector.md). Empty: blt's own worker.",
    )

    @contextmanager
    def client(self) -> Iterator[NetBackupClient]:
        """A connected client; the CA certificate, if any, is written to a
        file only for as long as the client lives."""
        with tempfile.TemporaryDirectory() as folder:
            ca = None
            if self.ca_certificate:
                ca = Path(folder) / "ca.pem"
                ca.write_text(self.ca_certificate)
            # Legacy key sizes: the same checks, through a transport of
            # our own (the SDK then leaves TLS to it).
            transport = (
                httpx.HTTPTransport(verify=legacy_context(ca))
                if self.legacy_keys and self.verify_tls
                else None
            )
            with NetBackupClient(
                self.url,
                api_key=self.api_key.get_secret_value(),
                verify_tls=self.verify_tls,
                ca_bundle=ca,
                transport=transport,
            ) as nbu:
                yield nbu


def jobs_primary(name: str, block: NetBackupPrimaryBlock, *, force_full: bool = False) -> str:
    """One primary's job collection, under the primary's own jobs lock."""
    with source_lock(f"netbackup-{name}.jobs") as held:
        if not held:
            logger.warning("A job run for NetBackup {} is already running, skipping.", name)
            return "skipped: already running"
        api = BltApiSettings()  # type: ignore[call-arg]
        with block.client() as nbu, api.client() as blt:
            run = run_netbackup_collection(
                NetBackupJobs(nbu), NetBackupStore(blt, name), force_full=force_full
            )
    return f"run {run.id}: {run.status}, {run.jobs_collected} jobs ({run.mode})"


@flow(
    name="blt-netbackup-jobs-primary",
    flow_run_name="netbackup-jobs-{primary}",
    retries=1,
    retry_delay_seconds=120,
)
def netbackup_jobs_primary(primary: str, full: bool = False) -> str:
    with logs_to_prefect(), concurrency(SLOTS, occupy=1, strict=False):
        block = cast(NetBackupPrimaryBlock, NetBackupPrimaryBlock.load(primary))
        return jobs_primary(primary, block, force_full=full)


def storage_primary(name: str, block: NetBackupPrimaryBlock) -> str:
    """One primary's storage, posted as one complete batch, under the
    primary's own lock."""
    with source_lock(f"netbackup-{name}.storage") as held:
        if not held:
            logger.warning("A storage run for NetBackup {} is already running, skipping.", name)
            return "skipped: already running"
        api = BltApiSettings()  # type: ignore[call-arg]
        collected_at = datetime.now(UTC)
        with block.client() as nbu, api.client() as blt:
            targets = netbackup_storage(nbu)
            result = StorageStore(blt, "netbackup", name).post(collected_at, targets)
    logger.info(
        "Storage: {} targets, {} new, {} samples, {} gone",
        len(targets),
        result.get("new"),
        result.get("samples"),
        result.get("gone"),
    )
    return f"{len(targets)} storage targets, {result.get('samples')} samples"


@flow(
    name="blt-netbackup-storage-primary",
    flow_run_name="netbackup-storage-{primary}",
    retries=1,
    retry_delay_seconds=120,
)
def netbackup_storage_primary(primary: str) -> str:
    with logs_to_prefect(), concurrency(SLOTS, occupy=1, strict=False):
        block = cast(NetBackupPrimaryBlock, NetBackupPrimaryBlock.load(primary))
        return storage_primary(primary, block)


def collectable_primaries() -> list[str]:
    with get_client(sync_client=True) as client:
        documents = client.read_block_documents_by_type(BLOCK_TYPE_SLUG, include_secrets=False)
    return sorted(
        document.name
        for document in documents
        if document.name and document.data.get("collect", True) is not False
    )


def _start_run(deployment: str, primary: str) -> str:
    label = "netbackup-jobs" if deployment == JOBS_ONE else "netbackup-storage"
    run = cast(
        FlowRun,
        run_deployment(
            deployment,
            parameters={"primary": primary},
            flow_run_name=f"{label}-{primary}",
            timeout=0,
            work_queue_name=source_queue(BLOCK_TYPE_SLUG, primary),
        ),
    )
    return str(run.id)


@flow(name="blt-netbackup-collect-storage")
def netbackup_collect_storage(
    primaries: list[str] | None = None, wait_minutes: float = 30
) -> dict[str, str]:
    """Storage from every NetBackup primary block (or the ones named), all
    at once, each in its own flow run."""
    names = primaries or collectable_primaries()
    if not names:
        # Checked here: fan_out given no names would fall back to the
        # CommCell blocks.
        get_run_logger().warning("No NetBackup primary blocks - see docs/storage.md")
        return {}
    return fan_out(
        names, STORAGE_ONE, "collect NetBackup storage", wait_minutes=wait_minutes, start=_start_run
    )


@flow(name="blt-netbackup-collect-jobs")
def netbackup_collect_jobs(
    primaries: list[str] | None = None, wait_minutes: float = 14
) -> dict[str, str]:
    """Jobs from every NetBackup primary block (or the ones named), all at
    once, each in its own flow run."""
    names = primaries or collectable_primaries()
    if not names:
        # Checked here: fan_out given no names would fall back to the
        # CommCell blocks.
        get_run_logger().warning("No NetBackup primary blocks - see docs/netbackup.md")
        return {}
    return fan_out(
        names, JOBS_ONE, "collect NetBackup jobs", wait_minutes=wait_minutes, start=_start_run
    )


@flow(name="blt-netbackup-check", flow_run_name="check-{primary}")
def netbackup_check(primary: str) -> str:
    """Signs in to the primary with its block's key and tries each read
    blt makes, logging one line for each. Fails only if it cannot sign in
    at all; a read the key may not make is a FAIL line, not a failed run."""
    with logs_to_prefect():
        block = cast(NetBackupPrimaryBlock, NetBackupPrimaryBlock.load(primary))
        logger.info("Checking NetBackup {} at {}", primary, block.url)
        with block.client() as nbu:
            checks = check_primary(nbu)
        for check in checks:
            (logger.info if check.ok else logger.warning)(check.line())
        passed = sum(c.ok for c in checks)
        summary = f"{passed} of {len(checks)} checks passed"
        if not checks[0].ok:
            problem = f"Could not sign in to {primary}: {checks[0].detail}"
            if weak_key(checks[0].detail) and not block.legacy_keys:
                problem += (
                    ". Its CA's key is below today's minimum (1024-bit, from an older install): "
                    "turn on 'Accept legacy key sizes' for it, or have the CA migrated."
                )
            other = fallback_url(block.url)
            if other and not answers(block.url) and answers(other):
                problem += (
                    f". Its API answers at {other} instead (NetBackup 10.5 can serve it on 443 "
                    "only): change its address to that."
                )
            raise RuntimeError(problem)
        logger.info(summary)
        return summary


@flow(name="blt-netbackup-fetch-ca", flow_run_name="fetch-ca")
def netbackup_fetch_ca(url: str) -> str:
    """Asks the primary at `url` for its CA certificate and logs each one
    it gives - PEM, subject, SHA-256 fingerprint - and whether it verifies
    the primary's TLS certificate at this address. Signs in to nothing.
    If the default port 1556 does not answer it tries 443, and says so."""
    with logs_to_prefect():
        asked = url
        url, certificates, why = fetch_ca_answering(url)
        if why:
            logger.warning("{} did not answer ({}); {} did.", asked, why, url)
            # The add page reads this line and takes the address (lib/admin.ts).
            logger.info("Answered at {} - use this address.", url)
        if not certificates:
            raise RuntimeError(f"{url} gave no CA certificate")
        logger.info("{} gave {} CA certificate(s)", url, len(certificates))
        trusted = 0
        for number, certificate in enumerate(certificates, 1):
            logger.info(
                "CA certificate {}: {} - SHA-256 {}",
                number,
                certificate.subject or "subject unreadable",
                certificate.sha256,
            )
            logger.info("{}", certificate.pem.strip())
            problem = verifies(url, certificate.pem)
            if problem is None:
                trusted += 1
                logger.info("CA certificate {} verifies this primary's TLS certificate.", number)
            elif weak_key(problem) and verifies(url, certificate.pem, legacy=True) is None:
                trusted += 1
                # The add page reads "only with legacy key sizes" (lib/admin.ts).
                logger.warning(
                    "CA certificate {} verifies this primary's TLS certificate only with legacy "
                    "key sizes accepted: {}",
                    number,
                    problem,
                )
            else:
                logger.warning("CA certificate {} does not verify it: {}", number, problem)
        return f"{len(certificates)} CA certificate(s), {trusted} verifying the primary"
