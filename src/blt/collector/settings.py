from __future__ import annotations

from datetime import datetime
from pathlib import Path

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
    # How far back the first run asks for. Commvault only returns what its
    # own job-history retention has kept, so "ten years" just means "all
    # of it".
    cv_initial_lookback_days: int = 3650
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
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # "CV_USERNAME=" left empty in the file means "not using this".
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _has_credentials(self) -> CollectorSettings:
        if not self.cv_access_token and not (self.cv_username and self.cv_password):
            raise ValueError("set CV_ACCESS_TOKEN, or both CV_USERNAME and CV_PASSWORD")
        return self


def commcell_env_path(commcell: str, config_dir: Path) -> Path:
    return config_dir / f"{commcell}.env"


def load_settings(commcell: str, config_dir: Path) -> CollectorSettings:
    commcell_env = commcell_env_path(commcell, config_dir)
    if not commcell_env.is_file():
        raise FileNotFoundError(
            f"{commcell_env} not found - copy config/commcell.env.example to it."
        )
    return CollectorSettings(_env_file=(config_dir / "blt.env", commcell_env))  # type: ignore[call-arg]
