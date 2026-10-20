"""Building the Commvault client a CommCell's settings describe - one
place, so the collector, the lookup and the flows all connect the same
way and a new setting only has to be wired in once.

Where the tokens live is the one thing that varies: a CommCell's .env
file when run by collect.sh, a Prefect block when run as a flow. Both
are a TokenStore.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, Protocol

from commvault_sdk import CommvaultClient, TokenSet

from .lock import wait_lock
from .settings import CollectorSettings, credentials_env_path
from .tokens import env_token_saver, read_tokens, warn_if_regeneration_due


class TokenStore(Protocol):
    """Where a CommCell's tokens are kept and renewed into.

    Renewal invalidates the old pair, so whoever renews must save the new
    one at once (`save`), and a run that shares the pair with others -
    an MSP's tenants - must take turns (`lock`) and pick up a renewal
    another run already made (`load`) instead of renewing again.
    """

    def save(self, tokens: TokenSet, raw_response: dict[str, Any]) -> None: ...

    def load(self) -> TokenSet | None: ...

    def lock(self) -> AbstractContextManager[None]: ...


class EnvTokenStore:
    """Tokens in a .env file, as collect.sh uses them."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._save = env_token_saver(path)
        self._lock_file = path.with_name(f".{path.stem}.token.lock")

    def save(self, tokens: TokenSet, raw_response: dict[str, Any]) -> None:
        self._save(tokens, raw_response)

    def load(self) -> TokenSet | None:
        return read_tokens(self.path)

    @contextmanager
    def lock(self) -> Iterator[None]:
        with wait_lock(self._lock_file):
            yield


def build_client(
    settings: CollectorSettings,
    commcell: str,
    tokens_in: TokenStore,
    *,
    allow_changes: bool = False,
) -> CommvaultClient:
    tokens = None
    if settings.cv_access_token:
        tokens = TokenSet(
            access_token=settings.cv_access_token.get_secret_value(),
            refresh_token=(
                settings.cv_refresh_token.get_secret_value() if settings.cv_refresh_token else None
            ),
            expires_at=settings.cv_token_expires_at,
            renewable_until=settings.cv_token_renewable_until,
        )
        warn_if_regeneration_due(commcell, settings.cv_token_renewable_until)
    return CommvaultClient(
        base_url=settings.cv_base_url,
        username=settings.cv_username,
        password=settings.cv_password.get_secret_value() if settings.cv_password else None,
        access_token=tokens,
        on_token_renew=tokens_in.save,
        token_reload=tokens_in.load,
        token_renew_lock=tokens_in.lock,
        extra_headers=settings.tenant_headers,
        verify_tls=settings.cv_verify_tls,
        ca_bundle=settings.cv_ca_bundle,
        page_size=settings.cv_page_size,
        timeout=settings.cv_timeout_seconds,
        allow_changes=allow_changes,
        deployment=settings.cv_deployment,
        max_calls_per_hour=settings.calls_per_hour,
    )


def commvault_client(
    settings: CollectorSettings,
    commcell: str,
    config_dir: Path,
    *,
    allow_changes: bool = False,
) -> CommvaultClient:
    """The client for a CommCell configured by .env files. Usually its
    tokens are in its own file; for an MSP, in one credentials file
    shared by a file per tenant."""
    store = EnvTokenStore(credentials_env_path(settings, commcell, config_dir))
    return build_client(settings, commcell, store, allow_changes=allow_changes)
