"""The few calls blt's admin pages make to Prefect's REST API: blocks
(sources), deployments (what runs when), flow runs and their logs.

Plain HTTP rather than the `prefect` package, which the API image does
not carry: the API only asks Prefect what it knows and tells it what to
do. Everything that talks to a backup product still happens in a flow,
on a worker - in production the API cannot reach the products at all
(docs/vision.md, network zones).

Settings: BLT_PREFECT_API_URL (http://prefect-server:4200/api) and
BLT_PREFECT_AUTH (user:password, Prefect's basic auth). Without the URL
the admin endpoints answer 503.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

# Prefect pages lists at 200 at most.
PAGE = 200


class PrefectError(Exception):
    """Prefect answered with an error, or could not be reached."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class PrefectApi:
    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    @classmethod
    def connect(cls, url: str, auth: str | None, timeout: float = 20.0) -> PrefectApi:
        basic = None
        if auth:
            user, _, password = auth.partition(":")
            basic = httpx.BasicAuth(user, password)
        return cls(httpx.Client(base_url=url.rstrip("/") + "/", auth=basic, timeout=timeout))

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        try:
            response = self._http.request(method, path.lstrip("/"), json=body)
        except httpx.HTTPError as error:
            raise PrefectError(f"Prefect could not be reached: {error}") from error
        if response.status_code >= 400:
            detail: Any = response.text
            try:
                detail = response.json().get("detail", detail)
            except ValueError:
                pass
            raise PrefectError(f"Prefect: {detail}", response.status_code)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # --- health -----------------------------------------------------------

    def version(self) -> str:
        return str(self._call("GET", "/admin/version"))

    # --- blocks -----------------------------------------------------------

    def block_documents(self, slugs: list[str]) -> list[dict[str, Any]]:
        """Every block of these types, secrets hidden."""
        found: list[dict[str, Any]] = []
        while True:
            page = self._call(
                "POST",
                "/block_documents/filter",
                {
                    "block_types": {"slug": {"any_": slugs}},
                    "include_secrets": False,
                    "sort": "NAME_ASC",
                    "limit": PAGE,
                    "offset": len(found),
                },
            )
            found.extend(page)
            if len(page) < PAGE:
                return found

    def block_document(self, slug: str, name: str) -> dict[str, Any] | None:
        try:
            return self._call(  # type: ignore[no-any-return]
                "GET", f"/block_types/slug/{slug}/block_documents/name/{name}?include_secrets=false"
            )
        except PrefectError as error:
            if error.status == 404:
                return None
            raise

    def block_schema(self, slug: str) -> tuple[str, str] | None:
        """(block type id, its newest schema's id), or None when no worker
        has registered the type yet."""
        try:
            block_type = self._call("GET", f"/block_types/slug/{slug}")
        except PrefectError as error:
            if error.status == 404:
                return None
            raise
        schemas = self._call(
            "POST",
            "/block_schemas/filter",
            {"block_schemas": {"block_type_id": {"any_": [block_type["id"]]}}, "limit": 1},
        )
        if not schemas:
            return None
        return str(block_type["id"]), str(schemas[0]["id"])

    def create_block(self, slug: str, name: str, data: dict[str, Any]) -> dict[str, Any]:
        ids = self.block_schema(slug)
        if ids is None:
            raise PrefectError(
                f"Prefect does not know the {slug} block type yet: the worker registers it when "
                "it starts.",
                409,
            )
        type_id, schema_id = ids
        return self._call(  # type: ignore[no-any-return]
            "POST",
            "/block_documents/",
            {
                "name": name,
                "block_type_id": type_id,
                "block_schema_id": schema_id,
                "data": data,
                "is_anonymous": False,
            },
        )

    def update_block(self, block_id: uuid.UUID | str, data: dict[str, Any]) -> None:
        """Changes only the fields given; the rest, secrets included, stay."""
        self._call(
            "PATCH", f"/block_documents/{block_id}", {"data": data, "merge_existing_data": True}
        )

    def delete_block(self, block_id: uuid.UUID | str) -> None:
        self._call("DELETE", f"/block_documents/{block_id}")

    # --- deployments ------------------------------------------------------

    def deployments(self) -> list[dict[str, Any]]:
        return self._call(  # type: ignore[no-any-return]
            "POST", "/deployments/filter", {"sort": "NAME_ASC", "limit": PAGE}
        )

    def deployment(self, deployment_id: uuid.UUID) -> dict[str, Any] | None:
        try:
            return self._call("GET", f"/deployments/{deployment_id}")  # type: ignore[no-any-return]
        except PrefectError as error:
            if error.status == 404:
                return None
            raise

    def deployment_by_name(self, flow: str, name: str) -> dict[str, Any] | None:
        try:
            return self._call("GET", f"/deployments/name/{flow}/{name}")  # type: ignore[no-any-return]
        except PrefectError as error:
            if error.status == 404:
                return None
            raise

    def flows(self, ids: list[str]) -> dict[str, str]:
        """Flow id -> flow name."""
        if not ids:
            return {}
        flows = self._call("POST", "/flows/filter", {"flows": {"id": {"any_": ids}}, "limit": PAGE})
        return {str(f["id"]): str(f["name"]) for f in flows}

    def set_paused(self, deployment_id: uuid.UUID, paused: bool) -> None:
        action = "pause_deployment" if paused else "resume_deployment"
        self._call("POST", f"/deployments/{deployment_id}/{action}")

    def create_flow_run(
        self,
        deployment_id: uuid.UUID,
        parameters: dict[str, Any],
        name: str | None = None,
        work_queue_name: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"parameters": parameters}
        if name:
            body["name"] = name
        if work_queue_name:
            # Prefect makes the queue if the pool has none by that name.
            body["work_queue_name"] = work_queue_name
        return self._call(  # type: ignore[no-any-return]
            "POST", f"/deployments/{deployment_id}/create_flow_run", body
        )

    # --- work queues ------------------------------------------------------

    def work_queues(self, pool: str) -> list[dict[str, Any]]:
        """The pool's queues, with when a worker last asked each for work."""
        try:
            return self._call(  # type: ignore[no-any-return]
                "POST", f"/work_pools/{pool}/queues/filter", {"limit": PAGE}
            )
        except PrefectError as error:
            if error.status == 404:
                return []  # no worker has started yet to make the pool
            raise

    # --- flow runs --------------------------------------------------------

    def flow_runs(
        self,
        *,
        deployment_ids: list[str] | None = None,
        state_types: list[str] | None = None,
        exclude_state_types: list[str] | None = None,
        sort: str = "EXPECTED_START_TIME_DESC",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        flow_runs: dict[str, Any] = {}
        state: dict[str, Any] = {}
        if state_types:
            state["any_"] = state_types
        if exclude_state_types:
            state["not_any_"] = exclude_state_types
        if state:
            flow_runs["state"] = {"type": state}
        body: dict[str, Any] = {
            "flow_runs": flow_runs,
            "sort": sort,
            "limit": min(limit, PAGE),
            "offset": offset,
        }
        if deployment_ids is not None:
            body["deployments"] = {"id": {"any_": deployment_ids}}
        return self._call("POST", "/flow_runs/filter", body)  # type: ignore[no-any-return]

    def flow_run(self, run_id: uuid.UUID) -> dict[str, Any] | None:
        try:
            return self._call("GET", f"/flow_runs/{run_id}")  # type: ignore[no-any-return]
        except PrefectError as error:
            if error.status == 404:
                return None
            raise

    def logs(
        self, run_id: uuid.UUID, *, offset: int = 0, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """A flow run's log lines, oldest first."""
        found: list[dict[str, Any]] = []
        while len(found) < limit:
            page = self._call(
                "POST",
                "/logs/filter",
                {
                    "logs": {"flow_run_id": {"any_": [str(run_id)]}},
                    "sort": "TIMESTAMP_ASC",
                    "limit": min(PAGE, limit - len(found)),
                    "offset": offset + len(found),
                },
            )
            found.extend(page)
            if len(page) < PAGE:
                break
        return found

    def force_cancel(self, run_id: uuid.UUID, message: str) -> None:
        """Cancelled at once, without asking a worker to stop it: for a
        run no worker is running any more."""
        self._call(
            "POST",
            f"/flow_runs/{run_id}/set_state",
            {
                "state": {"type": "CANCELLED", "name": "Cancelled", "message": message},
                "force": True,
            },
        )

    # --- concurrency limits -----------------------------------------------

    def concurrency_limit(self, name: str) -> dict[str, Any] | None:
        """A global concurrency limit: its `limit` and `active_slots`."""
        try:
            return self._call("GET", f"/v2/concurrency_limits/{name}")  # type: ignore[no-any-return]
        except PrefectError as error:
            if error.status == 404:
                return None
            raise

    def set_active_slots(self, name: str, active: int) -> None:
        self._call("PATCH", f"/v2/concurrency_limits/{name}", {"active_slots": active})

    def cancel(self, run_id: uuid.UUID) -> dict[str, Any]:
        return self._call(  # type: ignore[no-any-return]
            "POST",
            f"/flow_runs/{run_id}/set_state",
            {"state": {"type": "CANCELLING", "name": "Cancelling"}, "force": False},
        )
