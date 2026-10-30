"""The migrations build what the models describe: each table in its
product's schema, nothing left over in public."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

import blt.api.models
import blt.api.rubrik  # noqa: F401  (registers Rubrik's tables)


def test_migrations_match_the_models(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"include_schemas": True})
            differences = compare_metadata(context, SQLModel.metadata)
    finally:
        engine.dispose()
    assert differences == []


def test_commvault_tables_are_in_their_schema(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        commvault = set(inspector.get_table_names(schema=blt.api.models.SCHEMA))
        public = set(inspector.get_table_names(schema="public"))
    finally:
        engine.dispose()
    assert commvault == {
        "commcell",
        "collection_run",
        "job",
        "source_object",
        "instance_backup_report",
    }
    assert public == {"alembic_version"}


def test_rubrik_tables_are_in_their_schema(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        rubrik = set(inspect(engine).get_table_names(schema=blt.api.rubrik.SCHEMA))
    finally:
        engine.dispose()
    # source_object by the same name as Commvault's, so the two read alike.
    assert rubrik == {"account", "collection_run", "job", "source_object"}
