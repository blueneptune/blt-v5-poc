"""Administration: what used to need Prefect's own UI, through blt's.

- **Sources** - the Prefect blocks that say where each CommCell, Rubrik
  account and NetBackup primary is and how blt signs in. Listed without
  their secrets; a NetBackup primary can be added, changed and removed
  here, and any source turned on or off for the scheduled flows.
- **Schedules** - Prefect's deployments: what runs when, paused or not,
  its last and next run, and "run now".
- **Runs** - flow runs, with their logs, and cancelling one.

Anything that has to reach a backup product - checking a primary, fetching
its CA certificate - is a flow run on a worker, never a call from here
(docs/admin.md). The UI starts the run and shows its log.

A secret goes in once, when it is set, and never comes back out: Prefect
answers with `********` for a secret field, and these models report only
whether each one is set.
"""

# No `from __future__ import annotations`: see app.py.

import re
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field, SecretStr, field_validator

from blt.queues import (
    DEFAULT_QUEUE,
    SLOT_LIMIT,
    SOURCE_PARAMETERS,
    WORK_POOL,
    check_collector,
    queue_for,
)

from .docs import UNAUTHORIZED
from .prefect import PrefectApi, PrefectError

ADMIN = "admin"

SourceKind = Literal["commvault", "rubrik", "netbackup"]

# blt's block types, and the field of each that says where it is.
BLOCK_TYPES: dict[str, tuple[SourceKind, str]] = {
    "blt-commvault-commcell": ("commvault", "base_url"),
    "blt-rubrik-account": ("rubrik", "access_token_uri"),
    "blt-netbackup-primary": ("netbackup", "url"),
}
SLUG_OF: dict[str, str] = {kind: slug for slug, (kind, _) in BLOCK_TYPES.items()}

# The flows that reach a NetBackup primary for these pages
# (blt.flows.netbackup).
NETBACKUP_CHECK = ("blt-netbackup-check", "by-hand")
NETBACKUP_CA = ("blt-netbackup-fetch-ca", "by-hand")

# Prefect's own rule for block names.
BLOCK_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
NETBACKUP_PORT = 1556


class SourceBlockOut(BaseModel):
    """One source's Prefect block, without its secrets."""

    type: SourceKind = Field(description="The product.")
    name: str = Field(description="The block's name: blt's name for the source.")
    block_id: uuid.UUID = Field(description="The Prefect block document's id.")
    collect: bool = Field(description="Collected by the scheduled flows.")
    collector: str | None = Field(
        default=None,
        description="The remote collector that reaches it; none: blt's own worker.",
    )
    address: str | None = Field(description="Where it is: its API's URL.")
    verify_tls: bool | None = Field(default=None, description="TLS certificates are checked.")
    legacy_keys: bool | None = Field(
        default=None, description="NetBackup: legacy (1024-bit) TLS keys are accepted."
    )
    has_ca_certificate: bool | None = Field(
        default=None, description="NetBackup: a CA certificate is kept for it."
    )
    secrets_set: list[str] = Field(description="The secret fields that have a value.")
    updated: datetime | None = Field(default=None, description="When the block last changed.")


def normalise_netbackup_url(value: str) -> str:
    """`nbu01`, `nbu01:1556`, `https://nbu01` and the full
    https://nbu01:1556/netbackup all mean the same primary."""
    text = value.strip()
    if "://" not in text:
        text = f"https://{text}"
    parts = urlsplit(text)
    if parts.scheme != "https":
        raise ValueError("NetBackup's API is https only.")
    if not parts.hostname:
        raise ValueError("No host name in it.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Just the address: no user, query or fragment.")
    port = parts.port or NETBACKUP_PORT
    path = parts.path.rstrip("/") or "/netbackup"
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"https://{host}:{port}{path}"


def _check_pem(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    if "-----BEGIN CERTIFICATE-----" not in text or "PRIVATE KEY" in text:
        raise ValueError("A PEM certificate (-----BEGIN CERTIFICATE-----), and no private key.")
    return text + "\n"


class NetBackupPrimaryIn(BaseModel):
    """A NetBackup primary to add. It starts with collection off, so it
    can be checked first."""

    name: str = Field(
        description="blt's name for it: lower case letters, digits and dashes.",
        examples=["nbu-prod-01"],
    )
    url: str = Field(
        description="The primary: a host name, or https://<primary>:1556/netbackup.",
        examples=["nbu01.example.com"],
    )
    api_key: SecretStr = Field(description="An API key, made in NetBackup's web UI.")
    ca_certificate: str | None = Field(
        default=None, description="The primary's CA certificate (PEM), unless publicly signed."
    )
    verify_tls: bool = Field(default=True, description="Check the primary's TLS certificate.")
    legacy_keys: bool = Field(
        default=False,
        description="Accept TLS keys today's defaults call too weak (1024-bit RSA), for a primary "
        "whose NetBackup CA predates 2048-bit keys. Chain, expiry and host name are still "
        "checked.",
    )
    collect: bool = Field(default=False, description="Collected by the scheduled flows.")
    collector: str | None = Field(
        default=None,
        description="The remote collector that reaches it (docs/remote-collector.md); none: "
        "blt's own worker.",
        examples=["tools01"],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{"name": "nbu-prod-01", "url": "nbu01.example.com", "api_key": "A1b2..."}]
        }
    }

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not BLOCK_NAME.match(value):
            raise ValueError(
                "Lower case letters, digits and dashes, starting with a letter or digit."
            )
        return value

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return normalise_netbackup_url(value)

    @field_validator("ca_certificate")
    @classmethod
    def _ca(cls, value: str | None) -> str | None:
        return _check_pem(value)

    @field_validator("collector")
    @classmethod
    def _collector(cls, value: str | None) -> str | None:
        return check_collector(value)


class SourceBlockPatch(BaseModel):
    """What to change; anything left out stays as it is. `collect` and
    `collector` apply to every source; the rest are NetBackup's."""

    collect: bool | None = Field(default=None, description="Collected by the scheduled flows.")
    collector: str | None = Field(
        default=None,
        description="The remote collector that reaches it; an empty string: blt's own worker.",
    )
    url: str | None = Field(default=None, description="NetBackup: a new address.")
    api_key: SecretStr | None = Field(default=None, description="NetBackup: a new API key.")
    ca_certificate: str | None = Field(
        default=None, description="NetBackup: a new CA certificate; an empty string removes it."
    )
    verify_tls: bool | None = Field(
        default=None, description="NetBackup: check the primary's TLS certificate."
    )
    legacy_keys: bool | None = Field(
        default=None,
        description="NetBackup: "
        "Accept TLS keys today's defaults call too weak (1024-bit RSA), for a primary "
        "whose NetBackup CA predates 2048-bit keys. Chain, expiry and host name are still "
        "checked.",
    )

    model_config = {"json_schema_extra": {"examples": [{"collect": True}]}}

    @field_validator("url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return None if value is None else normalise_netbackup_url(value)

    @field_validator("collector")
    @classmethod
    def _collector(cls, value: str | None) -> str | None:
        # None: leave it; "": blt's own worker.
        return None if value is None else (check_collector(value) or "")

    @field_validator("ca_certificate")
    @classmethod
    def _ca(cls, value: str | None) -> str | None:
        return None if value is None else (_check_pem(value) or "")


class CaFetchIn(BaseModel):
    """A primary whose CA certificate to fetch."""

    url: str = Field(description="The primary, as for adding one.")
    collector: str | None = Field(
        default=None, description="Fetch it from this remote collector; none: blt's own worker."
    )

    model_config = {"json_schema_extra": {"examples": [{"url": "nbu01.example.com"}]}}

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return normalise_netbackup_url(value)

    @field_validator("collector")
    @classmethod
    def _collector(cls, value: str | None) -> str | None:
        return check_collector(value)


CollectorStatus = Literal["online", "offline", "paused", "never seen"]


class CollectorOut(BaseModel):
    """A worker that runs blt's flows: blt's own, or a remote collector."""

    name: str = Field(description="Its name: its work queue's (`default` for blt's own).")
    own: bool = Field(description="blt's own worker, in the blt pod.")
    status: CollectorStatus = Field(
        description="online: it asked for work in the last minute or so. offline: not lately "
        "(a remote one's tunnel may be down). never seen: sources name it, but it has never "
        "asked for work."
    )
    last_polled: datetime | None = Field(default=None, description="When it last asked.")
    sources: list[str] = Field(description="The sources it reaches, as type/name.")


class Schedule(BaseModel):
    """When a deployment runs."""

    cron: str | None = Field(default=None, description="A cron schedule.", examples=["7 * * * *"])
    interval_seconds: float | None = Field(default=None, description="Or every so many seconds.")
    timezone: str | None = Field(default=None, description="The cron schedule's time zone.")
    active: bool = Field(description="False: this schedule is switched off.")


class RunBrief(BaseModel):
    """A flow run, briefly."""

    id: uuid.UUID = Field(description="The flow run's id.")
    name: str = Field(description="Its name.")
    state_type: str | None = Field(description="SCHEDULED, RUNNING, COMPLETED, FAILED, ...")
    state_name: str | None = Field(description="Prefect's name for the state (Late, Crashed, ...).")
    expected_start_time: datetime | None = Field(default=None, description="When it was due.")
    start_time: datetime | None = Field(default=None, description="When it started.")
    end_time: datetime | None = Field(default=None, description="When it ended.")


class DeploymentOut(BaseModel):
    """Something Prefect runs: a flow, how it is started, and when."""

    id: uuid.UUID = Field(description="The deployment's id.")
    name: str = Field(description="Its name, e.g. hourly.")
    flow_name: str = Field(description="The flow it runs.")
    description: str | None = Field(default=None, description="What it does.")
    paused: bool = Field(description="No scheduled runs while paused.")
    schedules: list[Schedule] = Field(description="When it runs; none for by-hand ones.")
    parameters: dict[str, Any] = Field(description="The parameters it runs with by default.")
    parameter_schema: dict[str, Any] = Field(
        description="JSON schema of its parameters, for a run-now form."
    )
    last_run: RunBrief | None = Field(default=None, description="Its latest run that started.")
    next_run: RunBrief | None = Field(default=None, description="Its next scheduled run.")


class RunNowIn(BaseModel):
    """A run to start now."""

    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Parameters, over the deployment's own."
    )

    model_config = {"json_schema_extra": {"examples": [{"parameters": {"commcell": "cv-toaster"}}]}}


class FlowRunOut(BaseModel):
    """One flow run."""

    id: uuid.UUID = Field(description="The flow run's id.")
    name: str = Field(description="Its name.")
    flow_name: str | None = Field(description="The flow.")
    deployment_id: uuid.UUID | None = Field(description="The deployment that started it.")
    deployment_name: str | None = Field(description="That deployment's name.")
    state_type: str | None = Field(
        description="SCHEDULED, PENDING, RUNNING, COMPLETED, FAILED, ..."
    )
    state_name: str | None = Field(description="Prefect's name for the state.")
    message: str | None = Field(default=None, description="The state's message, if any.")
    parameters: dict[str, Any] = Field(description="What it was run with.")
    auto_scheduled: bool = Field(description="Started by its schedule, not by hand.")
    expected_start_time: datetime | None = Field(default=None, description="When it was due.")
    start_time: datetime | None = Field(default=None, description="When it started.")
    end_time: datetime | None = Field(default=None, description="When it ended.")
    total_run_time: float = Field(description="Seconds.")


class LogLine(BaseModel):
    """One line of a flow run's log."""

    timestamp: datetime = Field(description="When.")
    level: str = Field(description="INFO, WARNING, ERROR, ...")
    message: str = Field(description="The line.")


class FlowRunDetail(FlowRunOut):
    """A flow run and its log."""

    logs: list[LogLine] = Field(description="Its log lines, oldest first.")
    logs_truncated: bool = Field(description="More lines than were returned.")


class SlotsOut(BaseModel):
    """The collection slots: how many per-source runs may collect at once,
    and how many Prefect counts as taken."""

    name: str = Field(description="The Prefect global concurrency limit.")
    limit: int = Field(description="Slots there are (BLT_MAX_PARALLEL).")
    active: int = Field(description="Slots Prefect counts as taken.")


class StuckRunOut(FlowRunOut):
    """A run that has been running, starting or cancelling for too long."""

    stuck_minutes: int = Field(description="Minutes in its state so far.")


class RunHealthOut(BaseModel):
    """Whether runs are getting through: the collection slots against the
    runs that could be holding them, and runs that look stuck."""

    slots: SlotsOut | None = Field(
        description="The collection slots; none until blt's worker has started once."
    )
    collecting: int = Field(
        description="Per-source runs in RUNNING: at most this many can hold a slot."
    )
    leaked_slots: int = Field(
        description="Slots taken beyond the runs that could hold them: held by runs that "
        "are gone. Clearing gives them back."
    )
    stuck: list[StuckRunOut] = Field(
        description="Runs RUNNING longer than stuck_after_minutes, or PENDING or CANCELLING "
        "longer than 10 minutes, the longest first."
    )
    stuck_after_minutes: int = Field(description="How long a run may run before it counts.")
    healthy: bool = Field(description="No leaked slots and no stuck runs.")


class ClearStuckIn(BaseModel):
    """Stuck runs to cancel - the ids a health check listed - and the rule
    it used, so a run that has since moved on is left alone."""

    run_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=2000, description="Runs to cancel."
    )
    stuck_after_minutes: int = Field(
        default=120,
        ge=10,
        le=10080,
        description="The minutes the health check counted a RUNNING run stuck after.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "run_ids": ["0f4b5c3e-2a1d-4e6f-9b8a-7c6d5e4f3a2b"],
                    "stuck_after_minutes": 120,
                }
            ]
        }
    }


class ClearStuckOut(BaseModel):
    """What clearing did."""

    cancelled: list[uuid.UUID] = Field(description="The runs cancelled.")
    skipped: int = Field(description="Runs asked for that were no longer stuck, or gone.")
    slots_before: int | None = Field(description="Slots counted as taken before.")
    slots_after: int | None = Field(description="And after.")


RunState = Literal[
    "SCHEDULED",
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "CANCELLING",
    "CRASHED",
    "PAUSED",
]

# What makes a run stuck (GET /admin/runs/health).
ACTIVE_STATES = ["RUNNING", "PENDING", "CANCELLING"]
STUCK_AFTER_MINUTES = 120
WAITING_STUCK_MINUTES = 10
# Active runs read for a health check, at most.
ACTIVE_LIMIT = 2000

LEVELS = {10: "DEBUG", 20: "INFO", 30: "WARNING", 40: "ERROR", 50: "CRITICAL"}
LOG_LIMIT = 2000


def _is_secret(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("*") and set(value) == {"*"}


def source_out(document: dict[str, Any]) -> SourceBlockOut | None:
    slug = (document.get("block_type") or {}).get("slug")
    if slug not in BLOCK_TYPES:
        return None
    kind, address_field = BLOCK_TYPES[slug]
    data = document.get("data") or {}
    schema = (document.get("block_schema") or {}).get("fields") or {}
    secret_fields = set(schema.get("secret_fields") or [])
    secrets_set = sorted(
        key
        for key, value in data.items()
        if value is not None and (key in secret_fields or _is_secret(value))
    )
    return SourceBlockOut(
        type=kind,
        name=document["name"],
        block_id=document["id"],
        collect=data.get("collect", True) is not False,
        address=data.get(address_field),
        verify_tls=data.get("verify_tls"),
        legacy_keys=bool(data.get("legacy_keys")) if kind == "netbackup" else None,
        has_ca_certificate=bool(data.get("ca_certificate")) if kind == "netbackup" else None,
        collector=data.get("collector") or None,
        secrets_set=secrets_set,
        updated=document.get("updated"),
    )


def _brief(run: dict[str, Any] | None) -> RunBrief | None:
    if not run:
        return None
    state = run.get("state") or {}
    return RunBrief(
        id=run["id"],
        name=run["name"],
        state_type=state.get("type"),
        state_name=state.get("name"),
        expected_start_time=run.get("expected_start_time"),
        start_time=run.get("start_time"),
        end_time=run.get("end_time"),
    )


def _schedules(deployment: dict[str, Any]) -> list[Schedule]:
    found = []
    for item in deployment.get("schedules") or []:
        schedule = item.get("schedule") or {}
        interval = schedule.get("interval")
        found.append(
            Schedule(
                cron=schedule.get("cron"),
                interval_seconds=float(interval) if isinstance(interval, int | float) else None,
                timezone=schedule.get("timezone"),
                active=bool(item.get("active", True)),
            )
        )
    return found


def admin_router(
    get_prefect: Callable[[], Iterator[PrefectApi]], require_api_key: Callable[..., None]
) -> APIRouter:
    PrefectDep = Annotated[PrefectApi, Depends(get_prefect)]
    router = APIRouter(
        prefix="/admin",
        tags=[ADMIN],
        dependencies=[Depends(require_api_key)],
        responses={
            **UNAUTHORIZED,
            503: {"description": "Prefect is not set up for the API, or cannot be reached"},
        },
    )

    def guarded(call: Callable[[], Any]) -> Any:
        try:
            return call()
        except PrefectError as error:
            code = error.status or 503
            if code >= 500 or code in (401, 403):
                # Prefect's own trouble is not the caller's 4xx.
                code = status.HTTP_503_SERVICE_UNAVAILABLE if error.status is None else 502
            raise HTTPException(code, str(error)) from error

    def find_source(prefect: PrefectApi, kind: SourceKind, name: str) -> dict[str, Any]:
        document = guarded(lambda: prefect.block_document(SLUG_OF[kind], name))
        if document is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No {kind} source {name!r}")
        return document  # type: ignore[no-any-return]

    def start(
        prefect: PrefectApi,
        flow: tuple[str, str],
        parameters: dict[str, Any],
        name: str,
        queue: str,
    ) -> FlowRunOut:
        deployment = guarded(lambda: prefect.deployment_by_name(*flow))
        if deployment is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Prefect has no {flow[0]}/{flow[1]} deployment: the worker registers it when it "
                "starts.",
            )
        run = guarded(lambda: prefect.create_flow_run(deployment["id"], parameters, name, queue))
        return run_out(run, {deployment["flow_id"]: flow[0]}, {str(deployment["id"]): flow[1]})

    SourceType = Annotated[SourceKind, Path(description="The product.")]
    SourceName = Annotated[str, Path(description="The source's block name.")]

    @router.get("/sources", summary="Every source's block")
    def list_sources(prefect: PrefectDep) -> list[SourceBlockOut]:
        """Every CommCell, Rubrik account and NetBackup primary Prefect
        holds a block for, with whether each secret is set - never its
        value."""
        documents = guarded(lambda: prefect.block_documents(list(BLOCK_TYPES)))
        return [out for d in documents if (out := source_out(d))]

    @router.post(
        "/sources/netbackup",
        status_code=status.HTTP_201_CREATED,
        summary="Add a NetBackup primary",
        responses={409: {"description": "A source by that name exists"}},
    )
    def add_netbackup(body: NetBackupPrimaryIn, prefect: PrefectDep) -> SourceBlockOut:
        """Saves the primary's block. Collection starts off: check the
        primary first (`.../check`), then turn `collect` on."""
        if guarded(lambda: prefect.block_document(SLUG_OF["netbackup"], body.name)) is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"A NetBackup source {body.name!r} exists"
            )
        data = {
            "url": body.url,
            "api_key": body.api_key.get_secret_value(),
            "ca_certificate": body.ca_certificate,
            "verify_tls": body.verify_tls,
            "legacy_keys": body.legacy_keys,
            "collect": body.collect,
            "collector": body.collector,
        }
        guarded(lambda: prefect.create_block(SLUG_OF["netbackup"], body.name, data))
        return source_out(find_source(prefect, "netbackup", body.name))  # type: ignore[return-value]

    @router.patch("/sources/{source_type}/{source_name}", summary="Change a source's block")
    def change_source(
        source_type: SourceType,
        source_name: SourceName,
        body: SourceBlockPatch,
        prefect: PrefectDep,
    ) -> SourceBlockOut:
        """Changes only what is given. For CommCells and Rubrik accounts
        that is `collect`; their credentials are set with
        blt-prefect-import / blt-rubrik."""
        document = find_source(prefect, source_type, source_name)
        changes: dict[str, Any] = {}
        if body.collect is not None:
            changes["collect"] = body.collect
        if body.collector is not None:
            changes["collector"] = body.collector or None
        netbackup: dict[str, Any] = {
            "url": body.url,
            "api_key": body.api_key.get_secret_value() if body.api_key else None,
            "ca_certificate": body.ca_certificate,
            "verify_tls": body.verify_tls,
            "legacy_keys": body.legacy_keys,
        }
        given: dict[str, Any] = {k: v for k, v in netbackup.items() if v is not None}
        if given and source_type != "netbackup":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Only collect and collector can be changed here for a {source_type} source.",
            )
        if "ca_certificate" in given and given["ca_certificate"] == "":
            given["ca_certificate"] = None
        changes.update(given)
        if changes:
            guarded(lambda: prefect.update_block(document["id"], changes))
        return source_out(find_source(prefect, source_type, source_name))  # type: ignore[return-value]

    @router.delete(
        "/sources/{source_type}/{source_name}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Remove a source's block",
    )
    def remove_source(
        source_type: SourceType, source_name: SourceName, prefect: PrefectDep
    ) -> None:
        """Removes the block, so nothing collects from it any more. What
        was collected stays in blt."""
        document = find_source(prefect, source_type, source_name)
        guarded(lambda: prefect.delete_block(document["id"]))

    @router.post(
        "/sources/netbackup/{source_name}/check",
        status_code=status.HTTP_201_CREATED,
        summary="Check a NetBackup primary, from a worker",
    )
    def check_netbackup(source_name: SourceName, prefect: PrefectDep) -> FlowRunOut:
        """Starts blt-netbackup-check for the primary: it signs in with
        the block's key and tries each read blt makes (storage, SLP
        images, jobs), and logs what it found. Watch the run's log."""
        document = find_source(prefect, "netbackup", source_name)
        return start(
            prefect,
            NETBACKUP_CHECK,
            {"primary": source_name},
            f"check-{source_name}",
            queue_for(document.get("data") or {}),
        )

    @router.post(
        "/netbackup/ca-certificate",
        status_code=status.HTTP_201_CREATED,
        summary="Fetch a NetBackup primary's CA certificate, from a worker",
    )
    def fetch_netbackup_ca(body: CaFetchIn, prefect: PrefectDep) -> FlowRunOut:
        """Starts blt-netbackup-fetch-ca: it asks the primary for its CA
        certificate (which needs no sign-in) and logs it with its SHA-256
        fingerprint. Compare the fingerprint with the primary's own
        (`nbcertcmd -displayCACertDetail`) before trusting it."""
        host = urlsplit(body.url).hostname or "primary"
        return start(
            prefect,
            NETBACKUP_CA,
            {"url": body.url},
            f"fetch-ca-{host}",
            body.collector or DEFAULT_QUEUE,
        )

    def source_queue(prefect: PrefectApi, parameters: dict[str, Any]) -> str | None:
        for parameter, slug in SOURCE_PARAMETERS.items():
            name = parameters.get(parameter)
            if isinstance(name, str) and name:
                document = guarded(partial(prefect.block_document, slug, name))
                # No such source: the run fails on its own, saying so.
                return queue_for((document or {}).get("data") or {})
        return None

    @router.get("/collectors", summary="The workers that collect, and whether each is there")
    def list_collectors(prefect: PrefectDep) -> list[CollectorOut]:
        """blt's own worker and every remote collector: each work queue in
        blt's pool, and each collector a source names, with when it last
        asked for work and the sources it reaches."""
        documents = guarded(lambda: prefect.block_documents(list(BLOCK_TYPES)))
        reaches: dict[str, list[str]] = {DEFAULT_QUEUE: []}
        for document in documents:
            if out := source_out(document):
                reaches.setdefault(out.collector or DEFAULT_QUEUE, []).append(
                    f"{out.type}/{out.name}"
                )
        queues = {q["name"]: q for q in guarded(lambda: prefect.work_queues(WORK_POOL))}
        found = []
        for name in sorted(set(reaches) | set(queues), key=lambda n: (n != DEFAULT_QUEUE, n)):
            queue = queues.get(name)
            status_: CollectorStatus
            if queue is None or not queue.get("last_polled"):
                status_ = "never seen"
            elif queue.get("is_paused") or queue.get("status") == "PAUSED":
                status_ = "paused"
            elif queue.get("status") == "READY":
                status_ = "online"
            else:
                status_ = "offline"
            found.append(
                CollectorOut(
                    name=name,
                    own=name == DEFAULT_QUEUE,
                    status=status_,
                    last_polled=(queue or {}).get("last_polled"),
                    sources=sorted(reaches.get(name, [])),
                )
            )
        return found

    # --- schedules --------------------------------------------------------

    @router.get("/deployments", summary="What runs, and when")
    def list_deployments(prefect: PrefectDep) -> list[DeploymentOut]:
        """Every deployment, with its schedules, its last finished run and
        its next scheduled one."""
        deployments = guarded(prefect.deployments)
        flows = guarded(lambda: prefect.flows(sorted({str(d["flow_id"]) for d in deployments})))
        ids = [str(d["id"]) for d in deployments]
        last: dict[str, dict[str, Any]] = {}
        upcoming: dict[str, dict[str, Any]] = {}
        if ids:
            for run in guarded(
                lambda: prefect.flow_runs(
                    deployment_ids=ids, exclude_state_types=["SCHEDULED"], limit=200
                )
            ):
                last.setdefault(str(run.get("deployment_id")), run)
            for run in guarded(
                lambda: prefect.flow_runs(
                    deployment_ids=ids,
                    state_types=["SCHEDULED"],
                    sort="EXPECTED_START_TIME_ASC",
                    limit=200,
                )
            ):
                upcoming.setdefault(str(run.get("deployment_id")), run)
        return [
            DeploymentOut(
                id=d["id"],
                name=d["name"],
                flow_name=flows.get(str(d["flow_id"]), "?"),
                description=d.get("description"),
                paused=bool(d.get("paused")),
                schedules=_schedules(d),
                parameters=d.get("parameters") or {},
                parameter_schema=d.get("parameter_openapi_schema") or {},
                last_run=_brief(last.get(str(d["id"]))),
                next_run=_brief(upcoming.get(str(d["id"]))),
            )
            for d in deployments
        ]

    DeploymentId = Annotated[uuid.UUID, Path(description="The deployment's id.")]

    def find_deployment(prefect: PrefectApi, deployment_id: uuid.UUID) -> dict[str, Any]:
        deployment = guarded(lambda: prefect.deployment(deployment_id))
        if deployment is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No deployment {deployment_id}")
        return deployment  # type: ignore[no-any-return]

    @router.post(
        "/deployments/{deployment_id}/runs",
        status_code=status.HTTP_201_CREATED,
        summary="Run a deployment now",
    )
    def run_now(deployment_id: DeploymentId, body: RunNowIn, prefect: PrefectDep) -> FlowRunOut:
        """Starts a run now, with these parameters over its defaults. A
        run for one source (a `commcell`, `account` or `primary`
        parameter) goes to the collector that reaches that source."""
        deployment = find_deployment(prefect, deployment_id)
        queue = source_queue(prefect, {**(deployment.get("parameters") or {}), **body.parameters})
        run = guarded(
            lambda: prefect.create_flow_run(deployment_id, body.parameters, work_queue_name=queue)
        )
        flows = guarded(lambda: prefect.flows([str(deployment["flow_id"])]))
        return run_out(run, flows, {str(deployment["id"]): deployment["name"]})

    @router.post("/deployments/{deployment_id}/pause", summary="Pause a deployment's schedules")
    def pause(deployment_id: DeploymentId, prefect: PrefectDep) -> dict[str, bool]:
        """No scheduled runs until it is resumed. It stays paused when the
        worker restarts and deploys prefect.yaml again (prefect deploy
        leaves `paused` alone when prefect.yaml does not set it)."""
        find_deployment(prefect, deployment_id)
        guarded(lambda: prefect.set_paused(deployment_id, True))
        return {"paused": True}

    @router.post("/deployments/{deployment_id}/resume", summary="Resume a deployment's schedules")
    def resume(deployment_id: DeploymentId, prefect: PrefectDep) -> dict[str, bool]:
        """Its schedules make runs again."""
        find_deployment(prefect, deployment_id)
        guarded(lambda: prefect.set_paused(deployment_id, False))
        return {"paused": False}

    # --- runs -------------------------------------------------------------

    def names(
        prefect: PrefectApi, runs: list[dict[str, Any]]
    ) -> tuple[dict[str, str], dict[str, str]]:
        flows = guarded(lambda: prefect.flows(sorted({str(r["flow_id"]) for r in runs})))
        deployments = {str(d["id"]): d["name"] for d in guarded(prefect.deployments)}
        return flows, deployments

    @router.get("/runs", summary="Flow runs, newest first")
    def list_runs(
        prefect: PrefectDep,
        deployment_id: Annotated[
            uuid.UUID | None, Query(description="One deployment's runs.")
        ] = None,
        state: Annotated[
            list[RunState] | None, Query(description="Only runs in these states.")
        ] = None,
        scheduled: Annotated[
            bool, Query(description="Include runs that have not started yet.")
        ] = False,
        limit: Annotated[int, Query(ge=1, le=200, description="At most this many.")] = 50,
        offset: Annotated[int, Query(ge=0, description="Skip this many.")] = 0,
    ) -> list[FlowRunOut]:
        """Flow runs by when they were due, newest first: every
        deployment's, or one's."""
        runs = guarded(
            lambda: prefect.flow_runs(
                deployment_ids=[str(deployment_id)] if deployment_id else None,
                state_types=list(state) if state else None,
                exclude_state_types=None if scheduled or state else ["SCHEDULED"],
                limit=limit,
                offset=offset,
            )
        )
        flows, deployments = names(prefect, runs)
        return [run_out(r, flows, deployments) for r in runs]

    # --- run health -------------------------------------------------------

    def active_runs(prefect: PrefectApi) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        while len(found) < ACTIVE_LIMIT:
            page = guarded(
                partial(
                    prefect.flow_runs,
                    state_types=ACTIVE_STATES,
                    sort="EXPECTED_START_TIME_ASC",
                    limit=200,
                    offset=len(found),
                )
            )
            found.extend(page)
            if len(page) < 200:
                break
        return found

    def slots(prefect: PrefectApi) -> SlotsOut | None:
        limit = guarded(lambda: prefect.concurrency_limit(SLOT_LIMIT))
        if limit is None:
            return None
        return SlotsOut(
            name=SLOT_LIMIT, limit=int(limit.get("limit") or 0), active=int(limit["active_slots"])
        )

    def collecting(runs: list[dict[str, Any]]) -> int:
        return sum(
            1
            for r in runs
            if is_source_run(r)
            and ((r.get("state") or {}).get("type") or r.get("state_type")) == "RUNNING"
        )

    StuckAfter = Annotated[
        int,
        Query(
            ge=10,
            le=10080,
            description="Minutes a run may be RUNNING before it counts as stuck.",
        ),
    ]

    @router.get("/runs/health", summary="Are runs getting through?")
    def run_health(prefect: PrefectDep, stuck_after_minutes: StuckAfter = 120) -> RunHealthOut:
        """The collection slots against the runs that could hold them, and
        the runs that look stuck. A slot is leaked when a run that held it
        is gone - its worker restarted, or the Prefect server did while it
        kept them only in memory - and runs then queue for it for ever."""
        now = datetime.now(UTC)
        runs = active_runs(prefect)
        slot_state = slots(prefect)
        busy = collecting(runs)
        stuck = sorted(
            (
                (minutes, r)
                for r in runs
                if (minutes := stuck_minutes(r, stuck_after_minutes, now)) is not None
            ),
            key=lambda pair: -pair[0],
        )
        out: list[StuckRunOut] = []
        if stuck:
            flows, deployments = names(prefect, [r for _, r in stuck])
            out = [
                StuckRunOut(**run_out(r, flows, deployments).model_dump(), stuck_minutes=m)
                for m, r in stuck
            ]
        leaked = max(0, slot_state.active - busy) if slot_state else 0
        return RunHealthOut(
            slots=slot_state,
            collecting=busy,
            leaked_slots=leaked,
            stuck=out,
            stuck_after_minutes=stuck_after_minutes,
            healthy=not leaked and not out,
        )

    @router.post("/runs/clear-stuck", summary="Cancel stuck runs and give back leaked slots")
    def clear_stuck(body: ClearStuckIn, prefect: PrefectDep) -> ClearStuckOut:
        """Cancels the runs asked for that are still stuck by the same
        rule - at once, since no worker is running them - and then, if
        Prefect counts more slots taken than there are runs collecting,
        brings the count down to those runs. It never goes below them, so
        a run that holds a slot keeps it."""
        now = datetime.now(UTC)
        cancelled: list[uuid.UUID] = []
        for run_id in dict.fromkeys(body.run_ids):
            run = guarded(partial(prefect.flow_run, run_id))
            if run is None or stuck_minutes(run, body.stuck_after_minutes, now) is None:
                continue
            guarded(
                partial(
                    prefect.force_cancel,
                    run_id,
                    "Cleared as stuck from Administration -> Schedules.",
                )
            )
            cancelled.append(run_id)
        before = slots(prefect)
        after = before.active if before else None
        if before:
            busy = collecting(active_runs(prefect))
            if before.active > busy:
                guarded(lambda: prefect.set_active_slots(SLOT_LIMIT, busy))
                after = busy
        return ClearStuckOut(
            cancelled=cancelled,
            skipped=len(dict.fromkeys(body.run_ids)) - len(cancelled),
            slots_before=before.active if before else None,
            slots_after=after,
        )

    RunId = Annotated[uuid.UUID, Path(description="The flow run's id.")]

    @router.get("/runs/{run_id}", summary="A flow run and its log")
    def get_run(
        run_id: RunId,
        prefect: PrefectDep,
        log_offset: Annotated[int, Query(ge=0, description="Skip this many log lines.")] = 0,
    ) -> FlowRunDetail:
        """The run and its log lines, oldest first. Poll with `log_offset`
        set to the lines already shown to get only new ones."""
        run = guarded(lambda: prefect.flow_run(run_id))
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No flow run {run_id}")
        flows, deployments = names(prefect, [run])
        logs = guarded(lambda: prefect.logs(run_id, offset=log_offset, limit=LOG_LIMIT + 1))
        return FlowRunDetail(
            **run_out(run, flows, deployments).model_dump(),
            logs=[
                LogLine(
                    timestamp=line["timestamp"],
                    level=LEVELS.get(line.get("level", 20), str(line.get("level"))),
                    message=line.get("message", ""),
                )
                for line in logs[:LOG_LIMIT]
            ],
            logs_truncated=len(logs) > LOG_LIMIT,
        )

    @router.post("/runs/{run_id}/cancel", summary="Cancel a flow run")
    def cancel_run(run_id: RunId, prefect: PrefectDep) -> FlowRunOut:
        """Asks the worker to stop the run: it goes to CANCELLING, then
        CANCELLED once the worker has stopped it."""
        run = guarded(lambda: prefect.flow_run(run_id))
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No flow run {run_id}")
        guarded(lambda: prefect.cancel(run_id))
        run = guarded(lambda: prefect.flow_run(run_id))
        flows, deployments = names(prefect, [run])
        return run_out(run, flows, deployments)

    return router


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    when = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def stuck_minutes(run: dict[str, Any], stuck_after: int, now: datetime) -> int | None:
    """Minutes a run has been stuck in its state, or None if it is not:
    RUNNING for longer than stuck_after minutes, PENDING or CANCELLING
    (no worker took it up, or none is there to stop it) for longer than
    WAITING_STUCK_MINUTES."""
    state = run.get("state") or {}
    kind = state.get("type") or run.get("state_type")
    if kind not in ACTIVE_STATES:
        return None
    since = _when(run.get("start_time")) if kind == "RUNNING" else None
    since = since or _when(state.get("timestamp"))
    if since is None:
        return None
    minutes = int((now - since) / timedelta(minutes=1))
    limit = stuck_after if kind == "RUNNING" else WAITING_STUCK_MINUTES
    return minutes if minutes >= limit else None


def is_source_run(run: dict[str, Any]) -> bool:
    """A run for one source: the runs that take a collection slot."""
    return any(key in (run.get("parameters") or {}) for key in SOURCE_PARAMETERS)


def run_out(run: dict[str, Any], flows: dict[str, str], deployments: dict[str, str]) -> FlowRunOut:
    state = run.get("state") or {}
    deployment_id = run.get("deployment_id")
    return FlowRunOut(
        id=run["id"],
        name=run["name"],
        flow_name=flows.get(str(run.get("flow_id"))),
        deployment_id=deployment_id,
        deployment_name=deployments.get(str(deployment_id)) if deployment_id else None,
        state_type=state.get("type") or run.get("state_type"),
        state_name=state.get("name") or run.get("state_name"),
        message=state.get("message"),
        parameters=run.get("parameters") or {},
        auto_scheduled=bool(run.get("auto_scheduled")),
        expected_start_time=run.get("expected_start_time"),
        start_time=run.get("start_time"),
        end_time=run.get("end_time"),
        total_run_time=float(run.get("total_run_time") or 0),
    )
