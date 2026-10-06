"""Alembic environment. Driven by blt.api.migrate (no alembic.ini): the
URL comes from BLT_DATABASE_URL, the target schema from blt.api.models."""

from alembic import context
from sqlalchemy import create_engine
from sqlmodel import SQLModel

import blt.api.models  # noqa: F401  (registers the tables on SQLModel.metadata)
from blt.api.settings import ApiSettings

target_metadata = SQLModel.metadata


def run_migrations_online() -> None:
    engine = create_engine(ApiSettings().database_url.get_secret_value())  # type: ignore[call-arg]
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
