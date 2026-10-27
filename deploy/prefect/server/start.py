"""Start the Prefect server against its own database on blt's Postgres.

1. Wait for Postgres.
2. Make sure the `prefect` role and the `prefect` database (owned by it)
   exist, and that the role's password is the one in blt.env - so it is
   right on a Postgres that predates Prefect, and after the password is
   changed. Connects as blt's own Postgres user to do this.
3. Start the server with that database, building the connection URL
   here so a password with URL-special characters still works.

Nothing secret is printed.

Environment: PG_HOST, PG_PORT, POSTGRES_PASSWORD (blt's Postgres user),
PREFECT_DB_PASSWORD, PREFECT_LISTEN_HOST, PREFECT_LISTEN_PORT, plus the
PREFECT_* settings passed through to the server.

Without POSTGRES_PASSWORD step 2 is skipped: on a Kubernetes cluster the
Postgres operator creates the role and the database, and this server is
given only the prefect role's own password.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from urllib.parse import quote

import asyncpg

ROLE = DATABASE = "prefect"


async def connect(database: str, user: str, password: str) -> asyncpg.Connection:
    deadline = time.monotonic() + 120
    while True:
        try:
            return await asyncpg.connect(
                host=os.environ.get("PG_HOST", "127.0.0.1"),
                port=int(os.environ.get("PG_PORT", "5432")),
                user=user,
                password=password,
                database=database,
            )
        except (OSError, asyncpg.CannotConnectNowError) as exc:
            if time.monotonic() > deadline:
                sys.exit(f"Postgres did not accept connections within 120 s: {exc}")
            await asyncio.sleep(2)


async def ensure_database(admin_password: str, prefect_password: str) -> None:
    conn = await connect("blt", "blt", admin_password)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", ROLE)
        verb = "ALTER" if exists else "CREATE"
        # DDL takes no bind parameters; format() quotes the password.
        statement = await conn.fetchval(
            f"SELECT format('{verb} ROLE {ROLE} LOGIN PASSWORD %L', $1::text)", prefect_password
        )
        await conn.execute(statement)
        print(f"Prefect database role: {'updated' if exists else 'created'}", flush=True)
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", DATABASE):
            await conn.execute(f"CREATE DATABASE {DATABASE} OWNER {ROLE}")
            print("Prefect database: created", flush=True)
        else:
            print("Prefect database: already there", flush=True)
    finally:
        await conn.close()


def main() -> None:
    admin = os.environ.get("POSTGRES_PASSWORD")
    prefect_password = os.environ["PREFECT_DB_PASSWORD"]
    if admin:
        asyncio.run(ensure_database(admin, prefect_password))
    else:
        print("Prefect database: managed elsewhere, not checked", flush=True)

    host = os.environ.get("PG_HOST", "127.0.0.1")
    port = os.environ.get("PG_PORT", "5432")
    os.environ["PREFECT_SERVER_DATABASE_CONNECTION_URL"] = (
        f"postgresql+asyncpg://{ROLE}:{quote(prefect_password, safe='')}@{host}:{port}/{DATABASE}"
    )
    listen_host = os.environ.get("PREFECT_LISTEN_HOST", "0.0.0.0")
    listen_port = os.environ.get("PREFECT_LISTEN_PORT", "4200")
    os.execvp(
        "prefect", ["prefect", "server", "start", "--host", listen_host, "--port", listen_port]
    )


if __name__ == "__main__":
    main()
