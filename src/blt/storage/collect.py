"""One collection of a source's storage: read it, post it."""

from __future__ import annotations

from datetime import UTC, datetime

from commvault_sdk import CommvaultClient
from loguru import logger

from .commvault import commvault_storage
from .store import StorageStore


def collect_commvault_storage(cv: CommvaultClient, store: StorageStore) -> str:
    """Every storage pool on the CommCell, posted as one complete batch.
    A list that could not be read (one MediaAgent's associations, say)
    is logged; the pools are still posted, as complete - their numbers
    all come from one call that did answer."""
    collected_at = datetime.now(UTC)
    problems: list[str] = []
    targets = commvault_storage(cv, problems)
    for problem in problems:
        logger.warning("Storage: {}", problem)
    result = store.post(collected_at, targets)
    logger.info(
        "Storage: {} pools, {} new, {} samples, {} gone",
        len(targets),
        result.get("new"),
        result.get("samples"),
        result.get("gone"),
    )
    return f"{len(targets)} storage pools, {result.get('samples')} samples"
