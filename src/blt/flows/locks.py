"""Locks held in Prefect, so they hold across machines.

One run of each kind per source at a time, and one Commvault token
renewal per token pair at a time: before, each was a file lock in the
worker's temp directory (blt.collector.lock), which only kept apart runs
on one machine. Two machines of one collector, or a Kubernetes work pool
that gives every flow run its own pod, share no temp directory - so the
locks live where every run already goes: the Prefect server.

Each lock is a Prefect global concurrency limit of one slot, named
`blt-lock-<key>`, made the first time it is needed. A run holds the slot
under a lease the client renews while it runs; if the run's process dies
the lease runs out (LEASE_SECONDS) and the server takes the slot back, so
a dead run never holds a source for ever. The leases are kept on disk
(deploy/prefect/server/Containerfile), so a server restart forgets none.

`source_lock` is for a run that should not wait: if the slot is taken it
yields False and the run skips, as before. `wait_lock` waits its turn -
a token renewal must not be skipped.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from typing import cast

from loguru import logger
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import GlobalConcurrencyLimitCreate
from prefect.concurrency.sync import concurrency
from prefect.exceptions import ObjectAlreadyExists, ObjectNotFound

PREFIX = "blt-lock-"
# How long a run waits to see whether the lock is free before it skips.
# Long enough for a slot just released to be seen, short enough that a
# skipped run costs nothing.
TRY_SECONDS = 3.0
# How long a token renewal waits for another one to finish.
WAIT_SECONDS = 300.0
# A run's hold on a lock without a renewal from it: a run whose process
# died lets go after this long. Renewed in the background while it runs.
LEASE_SECONDS = 120.0


def lock_name(key: str) -> str:
    """`cv-toaster.collection` -> `blt-lock-cv-toaster.collection`."""
    if not key or any(c in key for c in "/%&<> "):
        raise ValueError(f"Not a lock key: {key!r}")
    return PREFIX + key


def ensure_lock(name: str) -> None:
    """Make the one-slot limit, unless it is there; a lock another run
    made at the same moment is fine."""
    with get_client(sync_client=True) as client:
        try:
            client.read_global_concurrency_limit_by_name(name)
            return
        except ObjectNotFound:
            pass
        try:
            client.create_global_concurrency_limit(GlobalConcurrencyLimitCreate(name=name, limit=1))
            logger.debug("Made the lock {}", name)
        except ObjectAlreadyExists:
            pass


def _hold(name: str, timeout: float) -> AbstractContextManager[None]:
    return cast(
        AbstractContextManager[None],
        concurrency(
            name, occupy=1, timeout_seconds=timeout, lease_duration=LEASE_SECONDS, strict=True
        ),
    )


@contextmanager
def source_lock(key: str) -> Iterator[bool]:
    """Yields True holding the lock `key`, or False - at once, without
    holding it - when another run holds it."""
    name = lock_name(key)
    ensure_lock(name)
    with ExitStack() as stack:
        try:
            stack.enter_context(_hold(name, TRY_SECONDS))
        except TimeoutError:
            yield False
            return
        yield True


@contextmanager
def wait_lock(key: str, timeout: float = WAIT_SECONDS) -> Iterator[None]:
    """Holds the lock `key`, waiting up to `timeout` seconds for it;
    raises TimeoutError if it is still held after that."""
    name = lock_name(key)
    ensure_lock(name)
    with _hold(name, timeout):
        yield
