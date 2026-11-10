"""Rubrik on Prefect: the account block and the collection flows.

One block per RSC account, of type "blt Rubrik account", named as blt
stores the account's data. It holds the service account - id, secret,
token address - and the few settings collection takes. Nothing in it
changes as runs happen: an RSC service account has no tokens to renew,
so there is no one-home rule to keep as there is for Commvault.

- rubrik_collect_account   - one account's jobs (blt-rubrik-collect-account
                             /per-account).
- rubrik_collect_jobs      - every account block, one flow run each, at the
                             same time (blt-rubrik-collect-jobs/hourly).
- rubrik_inventory_account - one account's objects (blt-rubrik-inventory-
                             account/per-account).
- rubrik_collect_inventory - every account block's objects (blt-rubrik-
                             collect-inventory/twice-daily).

They share Commvault's concurrency limit (flows.SLOTS), so the worker's
BLT_MAX_PARALLEL caps collection runs of every product together.
"""

from __future__ import annotations

from typing import cast

from loguru import logger
from prefect import flow
from prefect.blocks.core import Block
from prefect.client.orchestration import get_client
from prefect.client.schemas.objects import FlowRun
from prefect.concurrency.sync import concurrency
from prefect.deployments import run_deployment
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from rubrik_sdk import RubrikClient, ServiceAccount
from sdk_primer import APIClient

from blt.rubrik.collect import run_rubrik_collection, run_rubrik_inventory
from blt.rubrik.source import RubrikInventory, RubrikJobs
from blt.rubrik.store import RubrikStore

from .flows import SLOTS, fan_out_due, logs_to_prefect, source_queue, starts_at
from .locks import source_lock

BLOCK_TYPE_SLUG = "blt-rubrik-account"
COLLECT_ONE = "blt-rubrik-collect-account/per-account"
INVENTORY_ONE = "blt-rubrik-inventory-account/per-account"


class RubrikAccountBlock(Block):
    """One Rubrik Security Cloud account, reached with a service account."""

    _block_type_name = "blt Rubrik account"
    _block_type_slug = BLOCK_TYPE_SLUG
    _description = (
        "Where blt reaches one Rubrik Security Cloud account, and the service account it "
        "uses - the JSON file RSC gave when the service account was made."
    )

    client_id: str = Field(description="client_id from the service account file.")
    client_secret: SecretStr = Field(description="client_secret from the service account file.")
    access_token_uri: str = Field(
        description="access_token_uri from the file: https://<account>.my.rubrik.com/api/client_token"
    )
    service_account_name: str | None = Field(default=None, description="name from the file.")
    collect: bool = Field(default=True, description="Collected by the scheduled flows.")
    collector: str | None = Field(
        default=None,
        description="The remote collector that reaches it (its Prefect work queue; "
        "docs/remote-collector.md). Empty: blt's own worker.",
    )
    # When it is collected (blt.timing); all empty: its product's schedule.
    timing_every_minutes: int | None = Field(
        default=None, description="Collect jobs every so many minutes (blt.timing)."
    )
    timing_daily_at: str | None = Field(
        default=None, description="Or once a day at HH:MM, in timing_timezone."
    )
    timing_quiet_from: str | None = Field(
        default=None, description="No collection starts from HH:MM..."
    )
    timing_quiet_until: str | None = Field(default=None, description="...until HH:MM.")
    timing_timezone: str | None = Field(
        default=None, description="The time zone of the times above; empty: UTC."
    )
    initial_lookback_hours: int = Field(
        default=24, description="First run: jobs changed in this many hours."
    )
    overlap_minutes: int = Field(
        default=60, description="Delta runs reach this far behind the last run's start."
    )
    history_limit_days: int = Field(default=365, description="How far back a full run goes.")
    verify_tls: bool = True

    def service_account(self) -> ServiceAccount:
        return ServiceAccount(
            client_id=self.client_id,
            client_secret=self.client_secret,
            access_token_uri=self.access_token_uri,
            name=self.service_account_name,
        )

    @classmethod
    def from_service_account(cls, account: ServiceAccount) -> RubrikAccountBlock:
        return cls(
            client_id=account.client_id,
            client_secret=account.client_secret,
            access_token_uri=account.access_token_uri,
            service_account_name=account.name,
        )


class BltApiSettings(BaseSettings):
    """Where blt's API is: from the environment the flow runs in (the
    worker's), or config/blt.env on a host."""

    model_config = SettingsConfigDict(env_file="config/blt.env", extra="ignore")

    blt_api_url: str = "http://127.0.0.1:8088"
    blt_api_key: SecretStr
    blt_api_timeout_seconds: float = 120.0

    def client(self) -> APIClient:
        return APIClient(
            base_url=self.blt_api_url,
            default_headers={"X-API-Key": self.blt_api_key.get_secret_value()},
            timeout=self.blt_api_timeout_seconds,
        )


def collect_account(name: str, block: RubrikAccountBlock, *, force_full: bool = False) -> str:
    """One account's collection, with the per-account lock - shared by the
    flow and the blt-rubrik command."""
    with source_lock(f"rubrik-{name}.collection") as held:
        if not held:
            logger.warning("A collection for Rubrik account {} is already running, skipping.", name)
            return "skipped: already running"
        api = BltApiSettings()  # type: ignore[call-arg]
        with (
            RubrikClient(block.service_account(), verify_tls=block.verify_tls) as rsc,
            api.client() as blt,
        ):
            run = run_rubrik_collection(
                RubrikJobs(rsc),
                RubrikStore(blt, name),
                initial_lookback_hours=block.initial_lookback_hours,
                history_limit_days=block.history_limit_days,
                overlap_minutes=block.overlap_minutes,
                force_full=force_full,
            )
    return f"run {run.id}: {run.status}, {run.jobs_collected} jobs"


def inventory_account(name: str, block: RubrikAccountBlock) -> str:
    """One account's inventory, with its own lock: it can run while the
    account's jobs are being collected, not alongside another inventory."""
    with source_lock(f"rubrik-{name}.inventory") as held:
        if not held:
            logger.warning("An inventory for Rubrik account {} is already running, skipping.", name)
            return "skipped: already running"
        api = BltApiSettings()  # type: ignore[call-arg]
        with (
            RubrikClient(block.service_account(), verify_tls=block.verify_tls) as rsc,
            api.client() as blt,
        ):
            run = run_rubrik_inventory(RubrikInventory(rsc), RubrikStore(blt, name))
    return f"inventory run {run.id}: {run.status}, {run.jobs_collected} objects"


@flow(
    name="blt-rubrik-collect-account",
    flow_run_name="rubrik-{account}",
    retries=1,
    retry_delay_seconds=300,
)
def rubrik_collect_account(account: str, full: bool = False) -> str:
    with logs_to_prefect(), concurrency(SLOTS, occupy=1, strict=False):
        block = cast(RubrikAccountBlock, RubrikAccountBlock.load(account))
        return collect_account(account, block, force_full=full)


@flow(
    name="blt-rubrik-inventory-account",
    flow_run_name="rubrik-inventory-{account}",
    retries=1,
    retry_delay_seconds=300,
)
def rubrik_inventory_account(account: str) -> str:
    with logs_to_prefect(), concurrency(SLOTS, occupy=1, strict=False):
        block = cast(RubrikAccountBlock, RubrikAccountBlock.load(account))
        return inventory_account(account, block)


def collectable_accounts() -> list[str]:
    with get_client(sync_client=True) as client:
        documents = client.read_block_documents_by_type(BLOCK_TYPE_SLUG, include_secrets=False)
    return sorted(
        document.name
        for document in documents
        if document.name and document.data.get("collect", True) is not False
    )


def _start_run(deployment: str, account: str, delay_seconds: float = 0) -> str:
    label = "rubrik" if deployment == COLLECT_ONE else "rubrik-inventory"
    run = cast(
        FlowRun,
        run_deployment(
            deployment,
            parameters={"account": account},
            flow_run_name=f"{label}-{account}",
            timeout=0,
            scheduled_time=starts_at(delay_seconds),
            work_queue_name=source_queue(BLOCK_TYPE_SLUG, account),
        ),
    )
    return str(run.id)


@flow(name="blt-rubrik-collect-jobs")
def rubrik_collect_jobs(
    accounts: list[str] | None = None, wait_minutes: float = 50, interval_minutes: float = 60
) -> dict[str, str]:
    """Collect jobs from every Rubrik account block that is due (or the
    ones named, at once), each in its own flow run at its own time
    (blt.timing)."""
    return fan_out_due(
        accounts,
        COLLECT_ONE,
        "collect Rubrik jobs",
        "jobs",
        wait_minutes=wait_minutes,
        interval_minutes=interval_minutes,
        collectable=collectable_accounts,
        block_type_slug=BLOCK_TYPE_SLUG,
        start=_start_run,
        nothing="No Rubrik account blocks to collect - see docs/rubrik.md",
    )


@flow(name="blt-rubrik-collect-inventory")
def rubrik_collect_inventory(
    accounts: list[str] | None = None, wait_minutes: float = 110, interval_minutes: float = 720
) -> dict[str, str]:
    """Inventory from every Rubrik account block (or the ones named),
    each in its own flow run, spread over the start of the interval."""
    return fan_out_due(
        accounts,
        INVENTORY_ONE,
        "collect Rubrik inventory",
        "inventory",
        wait_minutes=wait_minutes,
        interval_minutes=interval_minutes,
        collectable=collectable_accounts,
        block_type_slug=BLOCK_TYPE_SLUG,
        start=_start_run,
        nothing="No Rubrik account blocks to inventory - see docs/rubrik.md",
    )
