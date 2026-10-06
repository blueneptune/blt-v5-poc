from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    """Read from the environment (deploy/up.sh passes these into the
    container): BLT_DATABASE_URL, BLT_API_KEY."""

    model_config = SettingsConfigDict(env_prefix="BLT_", extra="ignore")

    # e.g. postgresql+psycopg://blt:secret@127.0.0.1:5432/blt
    database_url: SecretStr
    api_key: SecretStr
