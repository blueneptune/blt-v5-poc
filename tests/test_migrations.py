"""The migrations build what the models describe: each table in its
product's schema, nothing left over in public."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

import blt.api.jobs  # noqa: F401  (registers common.job)
import blt.api.models
import blt.api.rubrik  # noqa: F401  (registers Rubrik's tables)
import blt.api.sources
import blt.api.storage  # noqa: F401  (registers common.storage)


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


def test_blt_tables_are_in_their_schema(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names(schema=blt.api.sources.SCHEMA))
    finally:
        engine.dispose()
    assert tables == {"source", "job", "storage", "storage_sample"}


def test_an_upgrade_past_a_mapping_change_projects_that_product_again() -> None:
    from blt.api.migrate import alembic_config, reprojections

    config = alembic_config()
    # From the release before the NetBackup job assets change: NetBackup.
    assert "netbackup" in reprojections(config, "e3b8f1a6c2d9")
    # Already past it, or a new database: nothing.
    assert reprojections(config, "d4a7c1e9b3f5") == set()
    assert reprojections(config, None) == set()
