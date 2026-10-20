"""A CommCell's settings and credentials as a Prefect block.

One block per CommCell, of type "blt Commvault CommCell", named as the
CommCell is (the name blt stores its data under). It holds what the
CommCell's .env file holds: the typed fields below for the settings
that matter, `options` for any other CV_* setting.

The tokens are kept in the block and renewed into it: when a run renews
the access token, the new pair is saved back to the block before
anything else happens, exactly as collect.sh saves it to the .env file.
A CommCell's tokens therefore live in one place or the other - see
docs/prefect.md before moving them.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from commvault_sdk import TokenSet
from loguru import logger
from prefect.blocks.core import Block
from pydantic import Field, SecretStr

from blt.collector.lock import wait_lock
from blt.collector.settings import CollectorSettings

BLOCK_TYPE_SLUG = "blt-commvault-commcell"

# Field -> the .env key it stands for.
ENV_KEYS = {
    "base_url": "CV_BASE_URL",
    "deployment": "CV_DEPLOYMENT",
    "credentials": "CV_CREDENTIALS",
    "account_id": "CV_ACCOUNT_ID",
    "tenant_guid": "CV_TENANT_GUID",
    "ring_id": "CV_RING_ID",
    "access_token": "CV_ACCESS_TOKEN",
    "refresh_token": "CV_REFRESH_TOKEN",
    "token_expires_at": "CV_TOKEN_EXPIRES_AT",
    "token_renewable_until": "CV_TOKEN_RENEWABLE_UNTIL",
    "username": "CV_USERNAME",
    "password": "CV_PASSWORD",
    "verify_tls": "CV_VERIFY_TLS",
}


class CommvaultCommCell(Block):
    """One Commvault CommServe, or one Commvault Cloud tenant."""

    _block_type_name = "blt Commvault CommCell"
    _block_type_slug = BLOCK_TYPE_SLUG
    _description = (
        "Where blt reaches one CommCell and the credentials it uses - what a CommCell's "
        ".env file held. Renewed tokens are saved back here."
    )

    base_url: str | None = Field(
        default=None,
        description="https://<host>/commandcenter/api, or https://api.metallic.io. "
        "Leave empty to take it from the credentials block.",
    )
    deployment: Literal["software", "saas"] | None = Field(
        default=None, description="software: a CommServe you run. saas: Commvault Cloud."
    )
    collect: bool = Field(
        default=True,
        description="Collected by the scheduled flows. Off for an MSP's own login, which only "
        "lends its credentials to the customer blocks.",
    )
    credentials: str | None = Field(
        default=None,
        description="Name of another block to take the URL, deployment and tokens from "
        "(an MSP login shared by its customers).",
    )
    account_id: str | None = Field(default=None, description="MSP: sent as lhAccountId.")
    tenant_guid: str | None = Field(default=None, description="MSP: sent as tenantGuid.")
    ring_id: str | None = Field(default=None, description="MSP: sent as ring-id.")
    access_token: SecretStr | None = None
    refresh_token: SecretStr | None = None
    token_expires_at: datetime | None = None
    token_renewable_until: datetime | None = None
    username: str | None = None
    password: SecretStr | None = None
    verify_tls: bool | None = None
    options: dict[str, str] = Field(
        default_factory=dict,
        description="Any other CV_* setting from the .env file, e.g. CV_PAGE_SIZE.",
    )
    last_renewal_response: SecretStr | None = Field(
        default=None,
        description="Commvault's last token-renewal answer, verbatim: the safety net "
        "config/.<name>.renewed.json is for collect.sh.",
    )

    def to_env(self) -> dict[str, str]:
        """This block as .env-style CV_* values; unset fields left out."""
        values: dict[str, str] = {}
        for field, key in ENV_KEYS.items():
            value = getattr(self, field)
            if value is None:
                continue
            if isinstance(value, SecretStr):
                value = value.get_secret_value()
            elif isinstance(value, datetime):
                value = value.isoformat()
            elif isinstance(value, bool):
                value = "true" if value else "false"
            values[key] = str(value)
        return {**self.options, **values}

    @classmethod
    def from_env(cls, values: dict[str, str | None]) -> CommvaultCommCell:
        """A block from a CommCell's .env values. Blank values are unset,
        and keys that are not CV_* settings are ignored."""
        present = {k: v.strip() for k, v in values.items() if v is not None and v.strip()}
        fields: dict[str, Any] = {}
        for field, key in ENV_KEYS.items():
            if key in present:
                fields[field] = present.pop(key)
        if "verify_tls" in fields:
            fields["verify_tls"] = fields["verify_tls"].lower() not in ("false", "0", "no")
        options = {k: v for k, v in present.items() if k.startswith("CV_")}
        return cls(**fields, options=options)

    def token_set(self) -> TokenSet | None:
        if self.access_token is None:
            return None
        return TokenSet(
            access_token=self.access_token.get_secret_value(),
            refresh_token=self.refresh_token.get_secret_value() if self.refresh_token else None,
            expires_at=self.token_expires_at,
            renewable_until=self.token_renewable_until,
        )


Loader = Callable[[str], CommvaultCommCell]


def _load(name: str) -> CommvaultCommCell:
    # Block.load is typed as possibly a coroutine; called from sync code
    # it is not.
    return cast(CommvaultCommCell, CommvaultCommCell.load(name))


def settings_from_blocks(name: str, load: Loader = _load) -> tuple[CollectorSettings, str]:
    """A CommCell's settings from its block (and its credentials block, if
    it names one), and the name of the block its tokens live in.

    Layered as the .env files are: the credentials block, then the
    CommCell's own block on top. Where blt's API is (BLT_API_URL,
    BLT_API_KEY) comes from the environment the flow runs in.
    """
    own = load(name)
    token_block = own.credentials or name
    values = load(own.credentials).to_env() if own.credentials else {}
    values.update(own.to_env())
    values.pop("CV_CREDENTIALS", None)
    # Through __init__, not model_validate: only __init__ also reads the
    # environment, which is where BLT_API_URL and BLT_API_KEY come from.
    fields: dict[str, Any] = {key.lower(): value for key, value in values.items()}
    settings = CollectorSettings(**fields)
    return settings, token_block


class BlockTokenStore:
    """Tokens kept in a block (connect.TokenStore).

    Turns are taken with a file lock in the worker's temp directory. That
    serialises renewals among flow runs on one worker, which is the setup
    this is built for; a second worker on another machine would need a
    Prefect concurrency limit instead.
    """

    def __init__(self, name: str, load: Loader = _load) -> None:
        self.name = name
        self._load = load
        self._lock_file = Path(tempfile.gettempdir()) / "blt-locks" / f"{name}.token.lock"

    def save(self, tokens: TokenSet, raw_response: dict[str, Any]) -> None:
        block = self._load(self.name)
        block.last_renewal_response = SecretStr(json.dumps(raw_response))
        block.access_token = SecretStr(tokens.access_token)
        if tokens.refresh_token:
            block.refresh_token = SecretStr(tokens.refresh_token)
        block.token_expires_at = tokens.expires_at
        if tokens.renewable_until:
            block.token_renewable_until = tokens.renewable_until
        block.save(self.name, overwrite=True)
        logger.info(
            "Saved the renewed Commvault token to block {} (expires {}, renewable until {})",
            self.name,
            tokens.expires_at or "unknown",
            tokens.renewable_until or "unknown",
        )

    def load(self) -> TokenSet | None:
        return self._load(self.name).token_set()

    @contextmanager
    def lock(self) -> Iterator[None]:
        with wait_lock(self._lock_file):
            yield
