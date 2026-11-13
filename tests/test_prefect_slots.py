"""The Prefect server's start gives back concurrency slots no lease holds
(deploy/prefect/server/start.py): a restored or moved prefect database
must not leave collection waiting for slots no run will ever free."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytest.importorskip("asyncpg")
_spec = importlib.util.spec_from_file_location(
    "prefect_server_start", ROOT / "deploy/prefect/server/start.py"
)
assert _spec and _spec.loader
start = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(start)

SLOTS = "11111111-1111-1111-1111-111111111111"
LOCK = "22222222-2222-2222-2222-222222222222"


def lease(directory: Path, name: str, limits: list[str], slots: int) -> None:
    (directory / f"{name}.json").write_text(
        json.dumps(
            {
                "id": name,
                "resource_ids": limits,
                "metadata": {"slots": slots},
                "expiration": "2026-11-13T00:00:00+00:00",
                "created_at": "2026-11-13T00:00:00+00:00",
            }
        )
    )


def test_leases_on_disk_are_counted_per_limit(tmp_path: Path) -> None:
    lease(tmp_path, "a", [SLOTS], 1)
    lease(tmp_path, "b", [SLOTS, LOCK], 1)
    (tmp_path / "expirations.json").write_text(json.dumps({"a": "2026-11-13T00:00:00+00:00"}))
    (tmp_path / "broken.json").write_text("{not json")
    assert start.held_slots(tmp_path) == Counter({SLOTS: 2, LOCK: 1})


def test_no_lease_folder_means_nothing_held(tmp_path: Path) -> None:
    assert start.held_slots(tmp_path / "missing") == Counter()


class FakeConnection:
    def __init__(self, rows: list[dict[str, Any]], *, tables: bool = True) -> None:
        self.rows = {row["id"]: dict(row) for row in rows}
        self.tables = tables

    async def fetchval(self, query: str) -> bool:
        return self.tables

    async def fetch(self, query: str) -> list[dict[str, Any]]:
        return [row for row in self.rows.values() if row["active_slots"] > 0]

    async def execute(self, query: str, limit_id: str, slots: int) -> None:
        self.rows[limit_id]["active_slots"] = slots


def test_slots_with_no_lease_are_given_back_and_held_ones_kept() -> None:
    conn = FakeConnection(
        [
            {"id": SLOTS, "name": "blt-commcell-runs", "active_slots": 4},
            {"id": LOCK, "name": "blt-lock-rsc.collection", "active_slots": 1},
        ]
    )
    # The restored database counts 4 slots and the lock taken; one run
    # here holds a slot under a lease.
    fixed = asyncio.run(start.reconcile_slots(conn, Counter({SLOTS: 1})))
    assert conn.rows[SLOTS]["active_slots"] == 1
    assert conn.rows[LOCK]["active_slots"] == 0
    assert fixed == ["blt-commcell-runs 4 -> 1", "blt-lock-rsc.collection 1 -> 0"]


def test_a_count_is_never_raised() -> None:
    conn = FakeConnection([{"id": SLOTS, "name": "blt-commcell-runs", "active_slots": 1}])
    assert asyncio.run(start.reconcile_slots(conn, Counter({SLOTS: 3}))) == []
    assert conn.rows[SLOTS]["active_slots"] == 1


def test_a_new_database_is_left_to_the_server() -> None:
    conn = FakeConnection([], tables=False)
    assert asyncio.run(start.reconcile_slots(conn, Counter())) == []
