"""Posts a collection of storage to blt's API (blt.api.storage)."""

from __future__ import annotations

from datetime import datetime

from sdk_primer import APIClient

from blt.schemas import StorageBatch, StorageIn


class StorageStore:
    def __init__(self, client: APIClient, source_type: str, source_name: str) -> None:
        self._client = client
        self._path = f"/v1/sources/{source_type}/{source_name}/storage"

    def post(
        self, collected_at: datetime, targets: list[StorageIn], *, complete: bool = True
    ) -> dict[str, int]:
        batch = StorageBatch(collected_at=collected_at, complete=complete, targets=targets)
        response = self._client.post(self._path, json=batch.model_dump(mode="json"))
        result: dict[str, int] = response.json()
        return result
