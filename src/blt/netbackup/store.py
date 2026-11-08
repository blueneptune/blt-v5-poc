"""The NetBackup job collector's view of the blt API: one method per
endpoint under /v1/sources/netbackup/{primary}. The same sdk-primer
APIClient the other collectors use, so a blip talking to the API is
retried the same way."""

from __future__ import annotations

from datetime import datetime

from sdk_primer import APIClient

from blt.schemas import UpsertResult

from .schemas import (
    NetBackupJobBatch,
    NetBackupJobIn,
    NetBackupLastRun,
    NetBackupRunFinish,
    NetBackupRunOut,
    NetBackupRunStart,
)


class NetBackupStore:
    def __init__(self, client: APIClient, primary: str) -> None:
        self._client = client
        self._base = f"/v1/sources/netbackup/{primary}"

    def last_run(self) -> NetBackupLastRun:
        return NetBackupLastRun.model_validate(self._client.get(f"{self._base}/last-run").json())

    def start_run(self, start: NetBackupRunStart) -> NetBackupRunOut:
        response = self._client.post(f"{self._base}/runs", json=start.model_dump(mode="json"))
        return NetBackupRunOut.model_validate(response.json())

    def finish_run(self, run_id: int, finish: NetBackupRunFinish) -> NetBackupRunOut:
        response = self._client.post(
            f"{self._base}/runs/{run_id}/finish", json=finish.model_dump(mode="json")
        )
        return NetBackupRunOut.model_validate(response.json())

    def upsert_jobs(
        self, run_id: int | None, collected_at: datetime, jobs: list[NetBackupJobIn]
    ) -> UpsertResult:
        batch = NetBackupJobBatch(run_id=run_id, collected_at=collected_at, jobs=jobs)
        response = self._client.post(f"{self._base}/jobs", json=batch.model_dump(mode="json"))
        return UpsertResult.model_validate(response.json())
