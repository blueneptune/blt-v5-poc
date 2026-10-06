"""Fixtures for the tests that need a real Postgres (the upsert is
Postgres-specific, so there is no SQLite stand-in).

Point BLT_TEST_DATABASE_URL at a scratch database - its public schema is
dropped and rebuilt from the migrations for every test:

    BLT_TEST_DATABASE_URL=postgresql+psycopg://blt:<pw>@127.0.0.1:5433/blt_test uv run pytest
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sdk_primer import APIClient
from sqlalchemy import create_engine, text

API_KEY = "test-key"


@pytest.fixture
def database_url(monkeypatch: pytest.MonkeyPatch) -> str:
    url = os.environ.get("BLT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("BLT_TEST_DATABASE_URL not set")
    monkeypatch.setenv("BLT_DATABASE_URL", url)
    monkeypatch.setenv("BLT_API_KEY", API_KEY)

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    from alembic import command

    from blt.api.migrate import alembic_config

    command.upgrade(alembic_config(), "head")
    return url


@pytest.fixture
def http(database_url: str) -> Iterator[TestClient]:
    """The API, in-process, with a valid key already attached."""
    from blt.api.app import create_app

    with TestClient(create_app(), headers={"X-API-Key": API_KEY}) as client:
        yield client


@pytest.fixture
def blt_api(http: TestClient) -> APIClient:
    """An sdk-primer APIClient whose requests land on the in-process API
    instead of a socket - TestClient is an httpx.Client, so it can stand
    in for the one APIClient builds for itself."""
    client = APIClient(base_url="http://testserver")
    client._http.close()
    client._http = http
    return client
