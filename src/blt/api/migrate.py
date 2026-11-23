"""`blt-migrate`: bring the database schema up to date.

The API container runs this before starting uvicorn. Postgres in the same
pod is usually still starting at that point, so it waits for a connection
first instead of failing the container on a race.

    blt-migrate                      # upgrade to head
    blt-migrate reproject            # rebuild common.job after a mapping change
    blt-migrate revision -m "..."    # autogenerate a new migration (dev)

After an upgrade that leaves common.job empty while product jobs exist
(the release that creates it), it fills common.job from the product
tables before the API starts - see blt.api.reproject. A migration that
comes with a change to a product's mapping (blt.common.mapping) says so
with a module-level `REPROJECT = ("netbackup",)`: the upgrade that
applies it projects that product's jobs again, once.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from .settings import ApiSettings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def current_revision(url: str) -> str | None:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def reprojections(config: Config, before: str | None) -> set[str]:
    """The products whose jobs the migrations after `before` ask to be
    projected again. None from a new database: there is nothing yet."""
    if before is None:
        return set()
    out: set[str] = set()
    for revision in ScriptDirectory.from_config(config).walk_revisions(before, "heads"):
        if revision.revision != before:
            out.update(getattr(revision.module, "REPROJECT", ()))
    return out


def wait_for_database(url: str, attempts: int = 30, delay: float = 2.0) -> None:
    engine = create_engine(url)
    try:
        for attempt in range(1, attempts + 1):
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                return
            except OperationalError:
                if attempt == attempts:
                    raise
                print(f"database not ready (attempt {attempt}/{attempts}), retrying in {delay}s")
                time.sleep(delay)
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="blt-migrate")
    sub = parser.add_subparsers(dest="action")
    revision = sub.add_parser("revision", help="autogenerate a migration from the models")
    revision.add_argument("-m", "--message", required=True)
    sub.add_parser("reproject", help="project every product job into common.job again")
    args = parser.parse_args()

    url = ApiSettings().database_url.get_secret_value()  # type: ignore[call-arg]
    wait_for_database(url)
    if args.action == "revision":
        command.revision(alembic_config(), message=args.message, autogenerate=True)
        return
    config = alembic_config()
    before = current_revision(url)
    command.upgrade(config, "head")
    asked = reprojections(config, before)
    # Imported here: it loads the API's models, which a revision does not need.
    from .reproject import needs_first_projection, reproject

    engine = create_engine(url)
    try:
        if args.action == "reproject" or needs_first_projection(engine):
            print(f"common.job: {reproject(engine)} jobs projected")
        elif asked:
            count = reproject(engine, products=asked)
            print(f"common.job: {count} {', '.join(sorted(asked))} jobs projected again")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
