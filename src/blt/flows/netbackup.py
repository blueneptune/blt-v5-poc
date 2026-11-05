"""NetBackup on Prefect: the primary block and the storage flows.

One block per NetBackup primary server, of type "blt NetBackup primary",
named as blt stores the primary's data. It holds the address, an API key
(made in the NetBackup web UI for a user with read access to storage),
and the primary's CA certificate when it is not publicly signed. An API
key does not renew, so as for Rubrik there is nothing to write back.

- netbackup_storage_primary - one primary's storage (blt-netbackup-
                              storage-primary/per-primary).
- netbackup_collect_storage - every primary block, one flow run each, at
                              the same time (blt-netbackup-collect-
                              storage/hourly).

Jobs are collected from NetBackup later; storage comes first
(docs/storage.md).
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from loguru import logger
from netbackup_sdk import NetBackupClient
from prefect import flow, get_run_logger
from prefect.blocks.core import Block
from prefect.client.orchestration import get_client
from prefect.client.schemas.objects import FlowRun
from prefect.concurrency.sync import concurrency
from prefect.deployments import run_deployment
from pydantic import Field, SecretStr

from blt.collector.lock import run_lock
from blt.storage.netbackup import netbackup_storage
from blt.storage.store import StorageStore

from .flows import SLOTS, fan_out, logs_to_prefect
from .rubrik import BltApiSettings

BLOCK_TYPE_SLUG = "blt-netbackup-primary"
STORAGE_ONE = "blt-netbackup-storage-primary/per-primary"
LOCKS = Path(tempfile.gettempdir()) / "blt-locks"


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
    collect: bool = Field(default=True, description="Collected by the scheduled flows.")

    @contextmanager
    def client(self) -> Iterator[NetBackupClient]:
        """A connected client; the CA certificate, if any, is written to a
        file only for as long as the client lives."""
        with tempfile.TemporaryDirectory() as folder:
            ca = None
            if self.ca_certificate:
                ca = Path(folder) / "ca.pem"
                ca.write_text(self.ca_certificate)
            with NetBackupClient(
                self.url,
                api_key=self.api_key.get_secret_value(),
                verify_tls=self.verify_tls,
                ca_bundle=ca,
            ) as nbu:
                yield nbu


def storage_primary(name: str, block: NetBackupPrimaryBlock) -> str:
    """One primary's storage, posted as one complete batch, under the
    primary's own lock."""
    with run_lock(LOCKS, f"netbackup-{name}.storage") as held:
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
    run = cast(
        FlowRun,
        run_deployment(
            deployment,
            parameters={"primary": primary},
            flow_run_name=f"netbackup-storage-{primary}",
            timeout=0,
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
