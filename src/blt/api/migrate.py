"""`blt-migrate`: bring the database schema up to date.

The API container runs this before starting uvicorn. Postgres in the same
pod is usually still starting at that point, so it waits for a connection
first instead of failing the container on a race.

    blt-migrate                      # upgrade to head
    blt-migrate reproject            # rebuild common.job after a mapping change
    blt-migrate revision -m "..."    # autogenerate a new migration (dev)

After an upgrade that leaves common.job empty while product jobs exist
(the release that creates it), it fills common.job from the product
tables before the API starts - see blt.api.reproject.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from .settings import ApiSettings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


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
    command.upgrade(alembic_config(), "head")
    # Imported here: it loads the API's models, which a revision does not need.
    from .reproject import needs_first_projection, reproject

    engine = create_engine(url)
    try:
        if args.action == "reproject" or needs_first_projection(engine):
            print(f"common.job: {reproject(engine)} jobs projected")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
