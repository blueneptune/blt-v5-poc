from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    """Read from the environment (deploy/up.sh passes these into the
    container): BLT_DATABASE_URL, BLT_API_KEY, and for the admin pages
    BLT_PREFECT_API_URL and BLT_PREFECT_AUTH."""

    model_config = SettingsConfigDict(env_prefix="BLT_", extra="ignore")

    # e.g. postgresql+psycopg://blt:secret@127.0.0.1:5432/blt
    database_url: SecretStr
    api_key: SecretStr
    # Prefect's REST API (http://127.0.0.1:4200/api) and its basic auth
    # (user:password). Without the URL the /v1/admin endpoints answer 503.
    prefect_api_url: str | None = None
    prefect_auth: SecretStr | None = None
