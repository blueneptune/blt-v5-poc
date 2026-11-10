"""Locks held in Prefect (blt.flows.locks), with a fake for Prefect's
global concurrency limits: one slot each, taken or free."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from prefect.exceptions import ObjectAlreadyExists, ObjectNotFound

from blt.flows import locks


class FakeLimits:
    def __init__(self) -> None:
        self.limits: dict[str, int] = {}
        self.taken: set[str] = set()
        self.made_by_another: set[str] = set()
        self.waited: list[float] = []

    # the client
    def __enter__(self) -> FakeLimits:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read_global_concurrency_limit_by_name(self, name: str) -> Any:
        if name not in self.limits:
            raise ObjectNotFound(http_exc=Exception("404"))
        return self.limits[name]

    def create_global_concurrency_limit(self, create: Any) -> None:
        if create.name in self.made_by_another:
            self.limits[create.name] = 1
            raise ObjectAlreadyExists(http_exc=Exception("409"))
        self.limits[create.name] = create.limit

    # prefect.concurrency.sync.concurrency
    @contextmanager
    def concurrency(self, name: str, **options: Any) -> Iterator[None]:
        assert options["strict"] is True and options["occupy"] == 1
        self.waited.append(options["timeout_seconds"])
        if name in self.taken:
            raise TimeoutError(f"{name} is taken")
        self.taken.add(name)
        try:
            yield
        finally:
            self.taken.discard(name)


@pytest.fixture
def prefect(monkeypatch: pytest.MonkeyPatch) -> FakeLimits:
    fake = FakeLimits()
    monkeypatch.setattr(locks, "get_client", lambda sync_client: fake)
    monkeypatch.setattr(locks, "concurrency", fake.concurrency)
    return fake


def test_one_run_per_source_and_kind(prefect: FakeLimits) -> None:
    with locks.source_lock("commvault-prod.collection") as first:
        assert first
        with locks.source_lock("commvault-prod.collection") as second:
            assert not second
        with locks.source_lock("commvault-prod.inventory") as other_kind:
            assert other_kind
        with locks.source_lock("commvault-dr.collection") as other_source:
            assert other_source
    with locks.source_lock("commvault-prod.collection") as again:
        assert again
    assert prefect.limits["blt-lock-commvault-prod.collection"] == 1
    assert set(prefect.waited) == {locks.TRY_SECONDS}


def test_a_run_that_fails_lets_go(prefect: FakeLimits) -> None:
    with pytest.raises(RuntimeError), locks.source_lock("rubrik-acme.collection") as held:
        assert held
        raise RuntimeError("the collection failed")
    assert prefect.taken == set()


def test_an_error_inside_a_held_lock_is_not_taken_for_a_held_lock(prefect: FakeLimits) -> None:
    with pytest.raises(TimeoutError, match="Commvault"), locks.source_lock("x.collection"):
        raise TimeoutError("Commvault did not answer")


def test_a_lock_another_run_made_at_the_same_moment_is_used(prefect: FakeLimits) -> None:
    prefect.made_by_another.add("blt-lock-netbackup-nbu.jobs")
    with locks.source_lock("netbackup-nbu.jobs") as held:
        assert held


def test_a_token_renewal_waits_its_turn(prefect: FakeLimits) -> None:
    with locks.wait_lock("commvault-prod.token"):
        assert "blt-lock-commvault-prod.token" in prefect.taken
    assert prefect.waited == [locks.WAIT_SECONDS]
    prefect.taken.add("blt-lock-commvault-prod.token")
    with pytest.raises(TimeoutError), locks.wait_lock("commvault-prod.token", timeout=1):
        pass


@pytest.mark.parametrize("key", ["", "a/b", "a b", "50%"])
def test_lock_keys_are_names_prefect_takes(key: str) -> None:
    with pytest.raises(ValueError):
        locks.lock_name(key)


def test_a_commcell_s_token_renewals_take_its_lock(prefect: FakeLimits) -> None:
    from blt.flows.blocks import BlockTokenStore

    with BlockTokenStore("cv-toaster", load=lambda name: None).lock():  # type: ignore[arg-type, return-value]
        assert prefect.taken == {"blt-lock-commvault-cv-toaster.token"}
