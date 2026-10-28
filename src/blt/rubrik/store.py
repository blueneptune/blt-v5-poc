"""The Rubrik collector's view of the blt API: one method per endpoint
under /rubrik/{account}. The same sdk-primer APIClient the Commvault
side uses (blt.collector.store), so a blip talking to the API is
retried the same way."""

from __future__ import annotations

from datetime import datetime

from sdk_primer import APIClient

from blt.schemas import UpsertResult

from .schemas import (
    RubrikJobBatch,
    RubrikJobIn,
    RubrikLastRun,
    RubrikRunFinish,
    RubrikRunOut,
    RubrikRunStart,
)


class RubrikStore:
    def __init__(self, client: APIClient, account: str) -> None:
        self._client = client
        self._base = f"/rubrik/{account}"

    def last_run(self) -> RubrikLastRun:
        return RubrikLastRun.model_validate(self._client.get(f"{self._base}/last-run").json())

    def start_run(self, start: RubrikRunStart) -> RubrikRunOut:
        response = self._client.post(f"{self._base}/runs", json=start.model_dump(mode="json"))
        return RubrikRunOut.model_validate(response.json())

    def finish_run(self, run_id: int, finish: RubrikRunFinish) -> RubrikRunOut:
        response = self._client.post(
            f"{self._base}/runs/{run_id}/finish", json=finish.model_dump(mode="json")
        )
        return RubrikRunOut.model_validate(response.json())

    def upsert_jobs(
        self, run_id: int | None, collected_at: datetime, jobs: list[RubrikJobIn]
    ) -> UpsertResult:
        batch = RubrikJobBatch(run_id=run_id, collected_at=collected_at, jobs=jobs)
        response = self._client.post(f"{self._base}/jobs", json=batch.model_dump(mode="json"))
        return UpsertResult.model_validate(response.json())
