"""common.source: every CommCell and RSC account gets one row, with a UUID,
made the first time a run starts for it - and the migration gives one to
each source collected before it existed."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from blt.api.migrate import alembic_config

RUN = {"mode": "delta", "started_at": "2026-11-02T12:00:00Z", "lookup_seconds": 3900}


def test_a_source_is_made_the_first_time_a_run_starts(http: TestClient) -> None:
    assert http.get("/v1/sources").json() == []
    for path in ("commvault/cv-toaster", "rubrik/work", "commvault/cv-toaster"):
        assert http.post(f"/v1/sources/{path}/runs", json=RUN).status_code == 201

    sources = http.get("/v1/sources").json()
    assert [(s["type"], s["name"]) for s in sources] == [
        ("commvault", "cv-toaster"),
        ("rubrik", "work"),
    ]
    assert all(uuid.UUID(s["id"]) for s in sources)
    rubrik = http.get("/v1/sources", params={"type": "rubrik"}).json()
    assert [s["name"] for s in rubrik] == ["work"]


def test_one_name_under_two_products_is_two_sources(http: TestClient) -> None:
    for path in ("commvault/lab", "rubrik/lab"):
        http.post(f"/v1/sources/{path}/runs", json=RUN)
    ids = {s["type"]: s["id"] for s in http.get("/v1/sources").json()}
    assert set(ids) == {"commvault", "rubrik"} and ids["commvault"] != ids["rubrik"]


def test_the_product_rows_point_at_their_source(http: TestClient, database_url: str) -> None:
    http.post("/v1/sources/commvault/cv-toaster/runs", json=RUN)
    http.post("/v1/sources/rubrik/work/runs", json=RUN)
    ids = {s["name"]: s["id"] for s in http.get("/v1/sources").json()}

    engine = create_engine(database_url)
    with engine.connect() as connection:
        commcell = connection.execute(text("SELECT source_id FROM commvault.commcell")).scalar()
        account = connection.execute(text("SELECT source_id FROM rubrik.account")).scalar()
    engine.dispose()
    assert str(commcell) == ids["cv-toaster"] and str(account) == ids["work"]


def test_sources_need_the_key(http: TestClient) -> None:
    assert http.get("/v1/sources", headers={"X-API-Key": "wrong"}).status_code == 401


def test_the_migration_gives_existing_sources_a_row(database_url: str) -> None:
    config = alembic_config()
    command.downgrade(config, "d4a2b6c8e9f1")
    created = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        for table in ("commvault.commcell", "rubrik.account"):
            connection.execute(
                text(f"INSERT INTO {table} (name, created_at) VALUES ('old', :at)"),
                {"at": created},
            )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT type, name, created_at, id FROM common.source ORDER BY type")
        ).all()
        linked = connection.execute(
            text(
                "SELECT count(*) FROM common.source s "
                "JOIN commvault.commcell c ON c.source_id = s.id AND s.type = 'commvault' "
            )
        ).scalar()
    engine.dispose()
    assert [(r.type, r.name, r.created_at) for r in rows] == [
        ("commvault", "old", created),
        ("rubrik", "old", created),
    ]
    assert linked == 1

    # And back down again cleanly.
    command.downgrade(config, "d4a2b6c8e9f1")
    command.upgrade(config, "head")
