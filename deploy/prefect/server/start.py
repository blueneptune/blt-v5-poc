"""Start the Prefect server against its own database on blt's Postgres.

1. Wait for Postgres.
2. Make sure the `prefect` role and the `prefect` database (owned by it)
   exist, and that the role's password is the one in blt.env - so it is
   right on a Postgres that predates Prefect, and after the password is
   changed. Connects as blt's own Postgres user to do this.
3. Give back concurrency slots no run holds (reconcile_slots).
4. Start the server with that database, building the connection URL
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
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
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


def held_slots(lease_dir: Path) -> Counter[str]:
    """The slots each concurrency limit's leases hold, by limit id, from
    the lease files the server keeps on disk
    (PREFECT_SERVER_CONCURRENCY_LEASE_STORAGE=...filesystem, in
    PREFECT_HOME/concurrency_leases). Expired leases count: the server
    gives their slots back itself once it is up."""
    held: Counter[str] = Counter()
    if not lease_dir.is_dir():
        return held
    for path in lease_dir.glob("*.json"):
        if path.name == "expirations.json":
            continue
        try:
            lease = json.loads(path.read_text())
            slots = int((lease.get("metadata") or {}).get("slots", 0))
            for limit_id in lease.get("resource_ids") or []:
                held[str(limit_id)] += slots
        except (OSError, ValueError, TypeError, AttributeError):
            continue  # one unreadable file: the server drops it itself
    return held


async def reconcile_slots(conn: asyncpg.Connection, held: Counter[str]) -> list[str]:
    """Take each limit's active slots down to what its leases hold.

    A slot is counted taken in the database and held by a lease on disk.
    When the database comes from somewhere the lease files did not - a
    restore of the prefect dump, a move to another machine, a lost
    volume - slots stay counted with no lease to give them back, and
    every run after waits for one for ever: collection stops. Run before
    the server serves, so nothing takes or gives a slot meanwhile. It
    only ever lowers a count, so a slot a lease holds is never freed."""
    if not await conn.fetchval("SELECT to_regclass('concurrency_limit_v2') IS NOT NULL"):
        return []  # a new database: the server makes its tables
    fixed = []
    for row in await conn.fetch(
        "SELECT id::text AS id, name, active_slots FROM concurrency_limit_v2 WHERE active_slots > 0"
    ):
        limit_id, name, counted = row["id"], row["name"], row["active_slots"]
        keep = min(counted, held.get(limit_id, 0))
        if keep < counted:
            await conn.execute(
                "UPDATE concurrency_limit_v2 SET active_slots = $2 WHERE id = $1::uuid",
                limit_id,
                keep,
            )
            fixed.append(f"{name} {counted} -> {keep}")
    return fixed


async def give_back_slots(prefect_password: str) -> None:
    home = Path(os.environ.get("PREFECT_HOME", str(Path.home() / ".prefect")))
    held = held_slots(home / "concurrency_leases")
    conn = await connect(DATABASE, ROLE, prefect_password)
    try:
        fixed = await reconcile_slots(conn, held)
    finally:
        await conn.close()
    if fixed:
        print("Concurrency slots no run holds, given back: " + ", ".join(fixed), flush=True)


def main() -> None:
    admin = os.environ.get("POSTGRES_PASSWORD")
    prefect_password = os.environ["PREFECT_DB_PASSWORD"]
    if admin:
        asyncio.run(ensure_database(admin, prefect_password))
    else:
        print("Prefect database: managed elsewhere, not checked", flush=True)
    try:
        asyncio.run(give_back_slots(prefect_password))
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - never a reason not to start
        print(f"Could not check concurrency slots ({type(exc).__name__}); carrying on.", flush=True)

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
