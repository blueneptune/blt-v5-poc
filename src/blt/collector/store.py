"""The collector's view of the blt API (blt.api.app) - one method per
endpoint, speaking blt.schemas in both directions. Uses the same
sdk-primer APIClient the Commvault side does, so a blip talking to the
API gets the same retry/backoff as a blip talking to the CommServe."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from sdk_primer import APIClient

from blt.schemas import (
    JobBatch,
    JobIn,
    JobOut,
    JobPatch,
    LastRun,
    RunFinish,
    RunOut,
    RunStart,
    UpsertResult,
)

_PAGE = 1000


class BltStore:
    def __init__(self, client: APIClient, commcell: str) -> None:
        self._client = client
        self._base = f"/commcells/{commcell}"

    def last_run(self) -> LastRun:
        return LastRun.model_validate(self._client.get(f"{self._base}/last-run").json())

    def start_run(self, start: RunStart) -> RunOut:
        response = self._client.post(f"{self._base}/runs", json=start.model_dump(mode="json"))
        return RunOut.model_validate(response.json())

    def finish_run(self, run_id: int, finish: RunFinish) -> RunOut:
        response = self._client.post(
            f"{self._base}/runs/{run_id}/finish", json=finish.model_dump(mode="json")
        )
        return RunOut.model_validate(response.json())

    def upsert_jobs(self, run_id: int, collected_at: datetime, jobs: list[JobIn]) -> UpsertResult:
        batch = JobBatch(run_id=run_id, collected_at=collected_at, jobs=jobs)
        response = self._client.post(f"{self._base}/jobs", json=batch.model_dump(mode="json"))
        return UpsertResult.model_validate(response.json())

    def iter_active_unseen(self, run_id: int) -> Iterator[JobOut]:
        """Jobs the database still has as active that `run_id` did not
        collect - i.e. the ones that need looking up individually."""
        offset = 0
        while True:
            response = self._client.get(
                f"{self._base}/jobs",
                params={
                    "active": "true",
                    "unseen_in_run": run_id,
                    "limit": _PAGE,
                    "offset": offset,
                },
            )
            page = [JobOut.model_validate(item) for item in response.json()]
            yield from page
            if len(page) < _PAGE:
                return
            offset += _PAGE

    def mark_job(self, job_id: int, patch: JobPatch) -> JobOut:
        response = self._client.request(
            "PATCH", f"{self._base}/jobs/{job_id}", json=patch.model_dump(mode="json")
        )
        return JobOut.model_validate(response.json())
