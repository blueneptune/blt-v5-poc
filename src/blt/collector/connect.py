"""Building the Commvault client a CommCell's settings describe - one
place, so the collector, the lookup and the lab all connect the same
way and a new setting only has to be wired in once."""

from __future__ import annotations

from pathlib import Path

from blt.commvault import CommvaultClient, TokenSet

from .settings import CollectorSettings, commcell_env_path
from .tokens import env_token_saver, warn_if_regeneration_due


def commvault_client(
    settings: CollectorSettings,
    commcell: str,
    config_dir: Path,
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
        on_token_renew=env_token_saver(commcell_env_path(commcell, config_dir)),
        verify_tls=settings.cv_verify_tls,
        ca_bundle=settings.cv_ca_bundle,
        page_size=settings.cv_page_size,
        timeout=settings.cv_timeout_seconds,
        allow_changes=allow_changes,
        deployment=settings.cv_deployment,
        max_calls_per_hour=settings.calls_per_hour,
    )
