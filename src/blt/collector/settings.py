from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CollectorSettings(BaseSettings):
    """Everything one collection run needs. Loaded from two files in the
    config directory - blt.env (shared: where the API is) then
    <commcell>.env (that CommCell's connection details) - with the second
    overriding the first, and real environment variables overriding both.
    """

    model_config = SettingsConfigDict(extra="ignore")

    cv_base_url: str
    # "software": a CommServe you run. "saas": Commvault Cloud (Metallic),
    # where CV_BASE_URL is https://api.metallic.io and the token travels
    # in a different header.
    cv_deployment: Literal["software", "saas"] = "software"
    # A ceiling on requests per hour to Commvault from one run. Unset
    # means none for software and SAAS_CALLS_PER_HOUR for SaaS, whose
    # tenants are limited to 1000 reads an hour across all API users.
    cv_max_calls_per_hour: int | None = None

    # For a managed service provider on Commvault Cloud, whose one login
    # reaches many customer tenants: which tenant this file is for. Sent
    # as the `lhAccountId` header (or `tenantGuid`, its documented
    # alternative; and `ring-id` for a customer spread over several
    # rings). Not used by a standalone customer.
    cv_account_id: str | None = None
    cv_tenant_guid: str | None = None
    cv_ring_id: str | None = None
    # Take the credentials from config/<name>.env instead of this file,
    # and write renewed tokens back there. For an MSP: one credentials
    # file, and a small file per tenant that names it.
    cv_credentials: str | None = None
    # Either an access token, or a username and password. If both are
    # set the access token is used.
    cv_access_token: SecretStr | None = None
    # With a refresh token the collector renews the access token itself
    # and rewrites these four lines in the .env file (blt.collector.tokens).
    cv_refresh_token: SecretStr | None = None
    cv_token_expires_at: datetime | None = None
    # The date a brand-new token has to be created by: renewal stops
    # working after it.
    cv_token_renewable_until: datetime | None = None
    cv_username: str | None = None
    cv_password: SecretStr | None = None
    cv_verify_tls: bool = True
    cv_ca_bundle: Path | None = None
    cv_page_size: int = 500
    # The first run for a CommCell is deliberately small: what is active
    # now plus what finished in this many hours. Older history is fetched
    # separately, a slice at a time (--backfill).
    cv_initial_lookback_hours: int = 24
    # How far back "all of it" means, for --full and as the limit a
    # backfill will not go behind. Commvault only returns what its own
    # job-history retention has kept.
    cv_history_limit_days: int = 3650
    # Backfill: the width of one time slice, how many slices one
    # invocation may fetch, and a pause between them - together, the
    # throttle that keeps a long history from being one heavy pull.
    cv_backfill_chunk_hours: int = 24
    cv_backfill_max_chunks: int = 30
    cv_backfill_pause_seconds: float = 1.0
    # Each delta run re-collects this much before the watermark, so a job
    # that finished right around the last run, or a clock that disagrees
    # with the CommServe's, can't open a gap. Upserts make the overlap free.
    cv_overlap_minutes: int = 60
    cv_timeout_seconds: float = 120.0

    blt_api_url: str = "http://127.0.0.1:8088"
    blt_api_key: SecretStr

    @field_validator(
        "cv_access_token",
        "cv_refresh_token",
        "cv_token_expires_at",
        "cv_token_renewable_until",
        "cv_username",
        "cv_password",
        "cv_max_calls_per_hour",
        "cv_account_id",
        "cv_tenant_guid",
        "cv_ring_id",
        "cv_credentials",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # "CV_USERNAME=" left empty in the file means "not using this".
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def tenant_headers(self) -> dict[str, str]:
        headers = {
            "lhAccountId": self.cv_account_id,
            "tenantGuid": self.cv_tenant_guid,
            "ring-id": self.cv_ring_id,
        }
        return {name: value for name, value in headers.items() if value}

    @property
    def calls_per_hour(self) -> int | None:
        if self.cv_max_calls_per_hour is not None:
            return self.cv_max_calls_per_hour or None
        return SAAS_CALLS_PER_HOUR if self.cv_deployment == "saas" else None

    @model_validator(mode="after")
    def _has_credentials(self) -> CollectorSettings:
        if not self.cv_access_token and not (self.cv_username and self.cv_password):
            raise ValueError("set CV_ACCESS_TOKEN, or both CV_USERNAME and CV_PASSWORD")
        return self


# Under Commvault Cloud's 1000 reads an hour, with room left for other
# users of the same tenant's API.
SAAS_CALLS_PER_HOUR = 800


def commcell_env_path(commcell: str, config_dir: Path) -> Path:
    return config_dir / f"{commcell}.env"


def load_settings(commcell: str, config_dir: Path) -> CollectorSettings:
    commcell_env = commcell_env_path(commcell, config_dir)
    if not commcell_env.is_file():
        raise FileNotFoundError(
            f"{commcell_env} not found - copy config/commcell.env.example to it."
        )
    files = [config_dir / "blt.env"]
    shared = (dotenv_values(commcell_env).get("CV_CREDENTIALS") or "").strip()
    if shared:
        credentials_env = commcell_env_path(shared, config_dir)
        if not credentials_env.is_file():
            raise FileNotFoundError(
                f"{commcell_env.name} says CV_CREDENTIALS={shared}, but {credentials_env} "
                "does not exist."
            )
        files.append(credentials_env)
    files.append(commcell_env)
    return CollectorSettings(_env_file=tuple(files))  # type: ignore[call-arg]


def credentials_env_path(settings: CollectorSettings, commcell: str, config_dir: Path) -> Path:
    """The file this CommCell's tokens live in, and are renewed into."""
    return commcell_env_path(settings.cv_credentials or commcell, config_dir)
