"""The admin endpoints (blt.api.admin), against a stand-in for Prefect's
REST API that answers the calls blt.api.prefect makes, in Prefect's
shapes. No database needed."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from blt.api.admin import normalise_netbackup_url
from blt.api.app import create_app
from blt.api.prefect import PrefectApi
from blt.api.settings import ApiSettings

KEY = "admin-tests"
PEM = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----"


def _id() -> str:
    return str(uuid.uuid4())


class FakePrefect:
    def __init__(self) -> None:
        self.block_types = {
            slug: {"id": _id(), "slug": slug}
            for slug in ("blt-commvault-commcell", "blt-rubrik-account", "blt-netbackup-primary")
        }
        self.secret_fields = {
            "blt-commvault-commcell": ["access_token", "refresh_token", "password"],
            "blt-rubrik-account": ["client_secret"],
            "blt-netbackup-primary": ["api_key"],
        }
        self.schemas = {
            t["id"]: {"id": _id(), "block_type_id": t["id"]} for t in self.block_types.values()
        }
        self.documents: dict[str, dict[str, Any]] = {}
        self.flows = {
            name: _id()
            for name in ("blt-collect-jobs", "blt-netbackup-check", "blt-netbackup-fetch-ca")
        }
        self.deployments: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.logs: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, str, Any]] = []
        # The blt pool's work queues; None: the pool does not exist yet.
        self.queues: list[dict[str, Any]] | None = []
        # The collection slots (a global concurrency limit); None: not made yet.
        self.slot_limit: dict[str, Any] | None = None
        self.add_deployment("blt-collect-jobs", "hourly", cron="7 * * * *")
        self.add_deployment("blt-netbackup-check", "by-hand", parameters={})
        self.add_deployment("blt-netbackup-fetch-ca", "by-hand", parameters={})

    def add_deployment(
        self,
        flow: str,
        name: str,
        cron: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> str:
        deployment_id = _id()
        self.deployments[deployment_id] = {
            "id": deployment_id,
            "name": name,
            "flow_id": self.flows[flow],
            "paused": False,
            "description": f"{flow} {name}",
            "schedules": [{"schedule": {"cron": cron, "timezone": "UTC"}, "active": True}]
            if cron
            else [],
            "parameters": {"wait_minutes": 30} if parameters is None else parameters,
            "parameter_openapi_schema": {"properties": {"wait_minutes": {"type": "number"}}},
        }
        return deployment_id

    def add_block(self, slug: str, name: str, data: dict[str, Any]) -> str:
        block_id = _id()
        self.documents[block_id] = {
            "id": block_id,
            "name": name,
            "data": data,
            "block_type": self.block_types[slug],
            "block_schema": {"fields": {"secret_fields": self.secret_fields[slug]}},
            "updated": "2026-11-05T12:00:00Z",
        }
        return block_id

    def add_run(self, deployment_id: str, state: str, when: str, **extra: Any) -> str:
        run_id = _id()
        deployment = self.deployments[deployment_id]
        self.runs[run_id] = {
            "id": run_id,
            "name": f"run-{len(self.runs)}",
            "flow_id": deployment["flow_id"],
            "deployment_id": deployment_id,
            "state": {"type": state, "name": state.title(), "message": None},
            "parameters": {},
            "auto_scheduled": state == "SCHEDULED",
            "expected_start_time": when,
            "start_time": None if state == "SCHEDULED" else when,
            "end_time": None,
            "total_run_time": 0,
            **extra,
        }
        return run_id

    def _hidden(self, document: dict[str, Any]) -> dict[str, Any]:
        secret = set(document["block_schema"]["fields"]["secret_fields"])
        data = {
            k: ("********" if k in secret and v is not None else v)
            for k, v in document["data"].items()
        }
        return {**document, "data": data}

    def handle(self, request: httpx.Request) -> httpx.Response:  # noqa: C901, PLR0911, PLR0912
        path = request.url.path.removeprefix("/api")
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        assert request.headers["authorization"].startswith("Basic ")
        parts = path.strip("/").split("/")

        if path == "/admin/version":
            return httpx.Response(200, json="3.8.8")
        if path == "/block_documents/filter":
            slugs = body["block_types"]["slug"]["any_"]
            found = [
                self._hidden(d) for d in self.documents.values() if d["block_type"]["slug"] in slugs
            ]
            return httpx.Response(200, json=sorted(found, key=lambda d: d["name"]))
        if parts[:2] == ["block_types", "slug"] and len(parts) == 3:
            found_type = self.block_types.get(parts[2])
            return httpx.Response(200, json=found_type) if found_type else httpx.Response(404)
        if parts[:2] == ["block_types", "slug"] and parts[3:5] == ["block_documents", "name"]:
            for document in self.documents.values():
                if document["block_type"]["slug"] == parts[2] and document["name"] == parts[5]:
                    return httpx.Response(200, json=self._hidden(document))
            return httpx.Response(404, json={"detail": "Block document not found"})
        if path == "/block_schemas/filter":
            type_id = body["block_schemas"]["block_type_id"]["any_"][0]
            return httpx.Response(200, json=[self.schemas[type_id]])
        if path == "/block_documents/" and request.method == "POST":
            slug = next(s for s, t in self.block_types.items() if t["id"] == body["block_type_id"])
            assert body["block_schema_id"] == self.schemas[body["block_type_id"]]["id"]
            block_id = self.add_block(slug, body["name"], body["data"])
            return httpx.Response(201, json=self.documents[block_id])
        if parts[0] == "block_documents" and len(parts) == 2:
            document = self.documents.get(parts[1])
            if document is None:
                return httpx.Response(404)
            if request.method == "PATCH":
                assert body["merge_existing_data"] is True
                document["data"] = {**document["data"], **body["data"]}
            elif request.method == "DELETE":
                del self.documents[parts[1]]
            return httpx.Response(204)

        if path == "/deployments/filter":
            return httpx.Response(200, json=list(self.deployments.values()))
        if parts[:2] == ["deployments", "name"]:
            flow_id = self.flows.get(parts[2])
            for d in self.deployments.values():
                if d["flow_id"] == flow_id and d["name"] == parts[3]:
                    return httpx.Response(200, json=d)
            return httpx.Response(404, json={"detail": "Deployment not found"})
        if parts[0] == "deployments" and len(parts) >= 2:
            deployment = self.deployments.get(parts[1])
            if deployment is None:
                return httpx.Response(404, json={"detail": "Deployment not found"})
            if len(parts) == 2:
                return httpx.Response(200, json=deployment)
            if parts[2] in ("pause_deployment", "resume_deployment"):
                deployment["paused"] = parts[2] == "pause_deployment"
                return httpx.Response(200, json=None)
            if parts[2] == "create_flow_run":
                run_id = self.add_run(parts[1], "SCHEDULED", "2026-11-05T12:30:00Z")
                run = self.runs[run_id]
                run["parameters"] = {**deployment["parameters"], **body["parameters"]}
                run["auto_scheduled"] = False
                run["work_queue_name"] = body.get("work_queue_name") or "default"
                if body.get("name"):
                    run["name"] = body["name"]
                return httpx.Response(201, json=run)
        if path == "/work_pools/blt/queues/filter":
            if self.queues is None:
                return httpx.Response(404, json={"detail": "Work pool not found"})
            return httpx.Response(200, json=self.queues)
        if path == "/flows/filter":
            ids = body["flows"]["id"]["any_"]
            names = {i: n for n, i in self.flows.items()}
            return httpx.Response(200, json=[{"id": i, "name": names[i]} for i in ids])

        if path == "/flow_runs/filter":
            runs = list(self.runs.values())
            if "deployments" in body:
                wanted = body["deployments"]["id"]["any_"]
                runs = [r for r in runs if r["deployment_id"] in wanted]
            state = body["flow_runs"].get("state", {}).get("type", {})
            if "any_" in state:
                runs = [r for r in runs if r["state"]["type"] in state["any_"]]
            if "not_any_" in state:
                runs = [r for r in runs if r["state"]["type"] not in state["not_any_"]]
            runs.sort(key=lambda r: r["expected_start_time"], reverse=body["sort"].endswith("DESC"))
            return httpx.Response(200, json=runs[body["offset"] : body["offset"] + body["limit"]])
        if parts[0] == "flow_runs" and len(parts) >= 2:
            run = self.runs.get(parts[1])
            if run is None:
                return httpx.Response(404, json={"detail": "Flow run not found"})
            if len(parts) == 3 and parts[2] == "set_state":
                run["state"] = {**body["state"], "message": None}
                return httpx.Response(201, json={"status": "ACCEPT"})
            return httpx.Response(200, json=run)
        if path == "/v2/concurrency_limits/blt-commcell-runs":
            if self.slot_limit is None:
                return httpx.Response(404, json={"detail": "Concurrency Limit not found"})
            if request.method == "PATCH":
                self.slot_limit.update(body)
                return httpx.Response(204)
            return httpx.Response(200, json=self.slot_limit)
        if path == "/logs/filter":
            run_id = body["logs"]["flow_run_id"]["any_"][0]
            lines = self.logs.get(run_id, [])
            return httpx.Response(200, json=lines[body["offset"] : body["offset"] + body["limit"]])
        return httpx.Response(500, json={"detail": f"the fake does not know {path}"})


@pytest.fixture
def prefect() -> FakePrefect:
    return FakePrefect()


def _settings() -> ApiSettings:
    return ApiSettings(
        database_url=SecretStr("postgresql+psycopg://nobody@127.0.0.1:1/none"),
        api_key=SecretStr(KEY),
    )


@pytest.fixture
def http(prefect: FakePrefect) -> Iterator[TestClient]:
    api = PrefectApi(
        httpx.Client(
            base_url="http://prefect/api/",
            transport=httpx.MockTransport(prefect.handle),
            auth=httpx.BasicAuth("admin", "pw"),
        )
    )
    with TestClient(create_app(_settings(), prefect=api), headers={"X-API-Key": KEY}) as client:
        yield client


# --- sources -------------------------------------------------------------


def test_sources_are_listed_without_their_secrets(http: TestClient, prefect: FakePrefect) -> None:
    prefect.add_block(
        "blt-commvault-commcell",
        "cv-toaster",
        {"base_url": "https://cv/api", "access_token": "SECRET-TOKEN-1", "refresh_token": None},
    )
    prefect.add_block(
        "blt-rubrik-account",
        "work",
        {
            "access_token_uri": "https://x/api/client_token",
            "client_secret": "SECRET-2",
            "collect": False,
        },
    )
    listed = http.get("/v1/admin/sources").json()
    assert [(s["type"], s["name"], s["collect"]) for s in listed] == [
        ("commvault", "cv-toaster", True),
        ("rubrik", "work", False),
    ]
    assert listed[0]["address"] == "https://cv/api"
    assert listed[0]["secrets_set"] == ["access_token"]
    assert "SECRET-" not in json.dumps(listed)


def test_a_netbackup_primary_is_added_switched_off(http: TestClient, prefect: FakePrefect) -> None:
    response = http.post(
        "/v1/admin/sources/netbackup",
        json={
            "name": "nbu-01",
            "url": "nbu01.example.com",
            "api_key": "the-key",
            "ca_certificate": PEM,
        },
    )
    assert response.status_code == 201, response.text
    added = response.json()
    assert added["type"] == "netbackup" and added["collect"] is False
    assert added["address"] == "https://nbu01.example.com:1556/netbackup"
    assert added["has_ca_certificate"] is True and added["secrets_set"] == ["api_key"]
    assert "the-key" not in response.text

    # Prefect got the key itself, once
    (stored,) = prefect.documents.values()
    assert stored["data"]["api_key"] == "the-key"
    assert stored["data"]["ca_certificate"].endswith("-----END CERTIFICATE-----\n")

    again = http.post(
        "/v1/admin/sources/netbackup",
        json={"name": "nbu-01", "url": "nbu01", "api_key": "k"},
    )
    assert again.status_code == 409


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "NBU 01"),
        ("url", "http://nbu01:1556/netbackup"),
        ("ca_certificate", "not a certificate"),
        ("ca_certificate", PEM + "\n-----BEGIN PRIVATE KEY-----"),
    ],
)
def test_a_bad_primary_is_refused(http: TestClient, field: str, value: str) -> None:
    body = {"name": "nbu-01", "url": "nbu01", "api_key": "k", field: value}
    assert http.post("/v1/admin/sources/netbackup", json=body).status_code == 422


def test_changes_touch_only_what_is_given(http: TestClient, prefect: FakePrefect) -> None:
    block_id = prefect.add_block(
        "blt-netbackup-primary",
        "nbu-01",
        {
            "url": "https://a:1556/netbackup",
            "api_key": "old",
            "ca_certificate": PEM,
            "collect": False,
        },
    )
    changed = http.patch("/v1/admin/sources/netbackup/nbu-01", json={"collect": True}).json()
    assert changed["collect"] is True
    assert prefect.documents[block_id]["data"]["api_key"] == "old"

    http.patch("/v1/admin/sources/netbackup/nbu-01", json={"api_key": "new", "ca_certificate": ""})
    data = prefect.documents[block_id]["data"]
    assert data["api_key"] == "new" and data["ca_certificate"] is None
    assert data["url"] == "https://a:1556/netbackup"


def test_only_collect_changes_for_a_commcell(http: TestClient, prefect: FakePrefect) -> None:
    prefect.add_block("blt-commvault-commcell", "cv", {"base_url": "https://cv/api"})
    assert http.patch("/v1/admin/sources/commvault/cv", json={"collect": False}).status_code == 200
    refused = http.patch("/v1/admin/sources/commvault/cv", json={"url": "nbu01"})
    assert refused.status_code == 422
    assert (
        http.patch("/v1/admin/sources/commvault/nope", json={"collect": False}).status_code == 404
    )


def test_a_source_is_removed(http: TestClient, prefect: FakePrefect) -> None:
    prefect.add_block("blt-netbackup-primary", "nbu-01", {"url": "https://a:1556/netbackup"})
    assert http.delete("/v1/admin/sources/netbackup/nbu-01").status_code == 204
    assert prefect.documents == {}
    assert http.delete("/v1/admin/sources/netbackup/nbu-01").status_code == 404


def test_checking_a_primary_starts_a_flow_run(http: TestClient, prefect: FakePrefect) -> None:
    prefect.add_block("blt-netbackup-primary", "nbu-01", {"url": "https://a:1556/netbackup"})
    run = http.post("/v1/admin/sources/netbackup/nbu-01/check")
    assert run.status_code == 201, run.text
    assert run.json()["flow_name"] == "blt-netbackup-check"
    assert run.json()["parameters"] == {"primary": "nbu-01"}
    assert run.json()["name"] == "check-nbu-01"
    assert http.post("/v1/admin/sources/netbackup/other/check").status_code == 404


def test_fetching_a_ca_certificate_starts_a_flow_run(http: TestClient) -> None:
    run = http.post("/v1/admin/netbackup/ca-certificate", json={"url": "nbu01:1556"}).json()
    assert run["flow_name"] == "blt-netbackup-fetch-ca"
    assert run["parameters"] == {"url": "https://nbu01:1556/netbackup"}


def test_without_the_worker_s_deployment_it_says_why(
    http: TestClient, prefect: FakePrefect
) -> None:
    prefect.deployments = {
        k: v
        for k, v in prefect.deployments.items()
        if v["flow_id"] != prefect.flows["blt-netbackup-fetch-ca"]
    }
    response = http.post("/v1/admin/netbackup/ca-certificate", json={"url": "nbu01"})
    assert response.status_code == 409
    assert "worker registers it" in response.json()["detail"]


# --- remote collectors (docs/remote-collector.md) ------------------------


def test_any_source_can_name_its_collector(http: TestClient, prefect: FakePrefect) -> None:
    block_id = prefect.add_block("blt-commvault-commcell", "cv", {"base_url": "https://cv/api"})
    assert http.get("/v1/admin/sources").json()[0]["collector"] is None

    moved = http.patch("/v1/admin/sources/commvault/cv", json={"collector": "tools01"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["collector"] == "tools01"
    assert prefect.documents[block_id]["data"]["collector"] == "tools01"

    # Leaving it out leaves it; an empty string - or "default" - is blt's own.
    http.patch("/v1/admin/sources/commvault/cv", json={"collect": False})
    assert prefect.documents[block_id]["data"]["collector"] == "tools01"
    back = http.patch("/v1/admin/sources/commvault/cv", json={"collector": ""}).json()
    assert back["collector"] is None and prefect.documents[block_id]["data"]["collector"] is None
    http.patch("/v1/admin/sources/commvault/cv", json={"collector": "default"})
    assert prefect.documents[block_id]["data"]["collector"] is None

    bad = http.patch("/v1/admin/sources/commvault/cv", json={"collector": "Tools 01"})
    assert bad.status_code == 422


def test_a_primary_added_with_a_collector_is_checked_by_it(
    http: TestClient, prefect: FakePrefect
) -> None:
    added = http.post(
        "/v1/admin/sources/netbackup",
        json={"name": "nbu-prod", "url": "nbu-prod", "api_key": "k", "collector": "tools01"},
    ).json()
    assert added["collector"] == "tools01"
    check = http.post("/v1/admin/sources/netbackup/nbu-prod/check").json()
    assert prefect.runs[check["id"]]["work_queue_name"] == "tools01"

    prefect.add_block("blt-netbackup-primary", "nbu-lab", {"url": "https://lab:1556/netbackup"})
    check = http.post("/v1/admin/sources/netbackup/nbu-lab/check").json()
    assert prefect.runs[check["id"]]["work_queue_name"] == "default"


def test_a_ca_certificate_is_fetched_by_the_collector_asked(
    http: TestClient, prefect: FakePrefect
) -> None:
    run = http.post(
        "/v1/admin/netbackup/ca-certificate", json={"url": "nbu-prod", "collector": "tools01"}
    ).json()
    assert prefect.runs[run["id"]]["work_queue_name"] == "tools01"


def test_run_now_for_one_source_goes_to_its_collector(
    http: TestClient, prefect: FakePrefect
) -> None:
    prefect.flows["blt-netbackup-jobs-primary"] = _id()
    per_primary = prefect.add_deployment("blt-netbackup-jobs-primary", "per-primary", parameters={})
    prefect.add_block(
        "blt-netbackup-primary", "nbu-prod", {"url": "https://p:1556/netbackup", "collector": "t1"}
    )
    run = http.post(
        f"/v1/admin/deployments/{per_primary}/runs", json={"parameters": {"primary": "nbu-prod"}}
    ).json()
    assert prefect.runs[run["id"]]["work_queue_name"] == "t1"

    # A parent (all sources at once) runs on blt's own worker, and fans
    # out from there.
    run = http.post(f"/v1/admin/deployments/{_hourly(prefect)}/runs", json={}).json()
    assert prefect.runs[run["id"]]["work_queue_name"] == "default"


def test_collectors_are_listed_with_whether_each_is_there(
    http: TestClient, prefect: FakePrefect
) -> None:
    prefect.add_block("blt-commvault-commcell", "cv-lab", {"base_url": "https://cv/api"})
    prefect.add_block("blt-netbackup-primary", "nbu-a", {"url": "u", "collector": "tools01"})
    prefect.add_block("blt-rubrik-account", "rsc", {"access_token_uri": "u", "collector": "dmz"})
    prefect.add_block("blt-netbackup-primary", "nbu-b", {"url": "u", "collector": "later"})
    prefect.queues = [
        {"name": "default", "status": "READY", "last_polled": "2026-11-09T07:00:00Z"},
        {"name": "tools01", "status": "NOT_READY", "last_polled": "2026-11-09T05:00:00Z"},
        {"name": "dmz", "status": "READY", "last_polled": "2026-11-09T07:00:01Z"},
        {"name": "old", "status": "PAUSED", "is_paused": True, "last_polled": None},
    ]
    found = {c["name"]: c for c in http.get("/v1/admin/collectors").json()}
    assert list(found) == ["default", "dmz", "later", "old", "tools01"]
    assert found["default"]["own"] is True
    assert found["default"]["sources"] == ["commvault/cv-lab"]
    assert (found["default"]["status"], found["dmz"]["status"]) == ("online", "online")
    assert found["tools01"]["status"] == "offline"
    assert found["tools01"]["sources"] == ["netbackup/nbu-a"]
    assert found["later"]["status"] == "never seen"  # named, never started
    assert found["old"]["status"] == "never seen" and found["old"]["sources"] == []

    prefect.queues = None  # no worker has started yet
    only = http.get("/v1/admin/collectors").json()
    assert {c["name"]: c["status"] for c in only}["default"] == "never seen"


@pytest.mark.parametrize(
    ("given", "url"),
    [
        ("nbu01", "https://nbu01:1556/netbackup"),
        ("nbu01.example.com:443", "https://nbu01.example.com:443/netbackup"),
        ("https://nbu01:1556/netbackup/", "https://nbu01:1556/netbackup"),
        (" https://NBU01 ", "https://nbu01:1556/netbackup"),
    ],
)
def test_netbackup_addresses(given: str, url: str) -> None:
    assert normalise_netbackup_url(given) == url


# --- schedules and runs --------------------------------------------------


def _hourly(prefect: FakePrefect) -> str:
    return next(i for i, d in prefect.deployments.items() if d["name"] == "hourly")


def test_deployments_with_their_last_and_next_runs(http: TestClient, prefect: FakePrefect) -> None:
    hourly = _hourly(prefect)
    prefect.add_run(hourly, "COMPLETED", "2026-11-05T10:07:00Z")
    prefect.add_run(hourly, "FAILED", "2026-11-05T11:07:00Z")
    prefect.add_run(hourly, "SCHEDULED", "2026-11-05T13:07:00Z")
    prefect.add_run(hourly, "SCHEDULED", "2026-11-05T12:07:00Z")
    listed = {d["name"]: d for d in http.get("/v1/admin/deployments").json()}
    found = listed["hourly"]
    assert found["flow_name"] == "blt-collect-jobs"
    assert found["schedules"] == [
        {"cron": "7 * * * *", "interval_seconds": None, "timezone": "UTC", "active": True}
    ]
    assert found["last_run"]["state_type"] == "FAILED"
    assert found["next_run"]["expected_start_time"] == "2026-11-05T12:07:00Z"
    assert listed["by-hand"]["last_run"] is None


def test_pause_resume_and_run_now(http: TestClient, prefect: FakePrefect) -> None:
    hourly = _hourly(prefect)
    assert http.post(f"/v1/admin/deployments/{hourly}/pause").json() == {"paused": True}
    assert prefect.deployments[hourly]["paused"] is True
    http.post(f"/v1/admin/deployments/{hourly}/resume")
    assert prefect.deployments[hourly]["paused"] is False

    run = http.post(
        f"/v1/admin/deployments/{hourly}/runs", json={"parameters": {"wait_minutes": 5}}
    )
    assert run.status_code == 201
    assert run.json()["parameters"] == {"wait_minutes": 5}
    assert run.json()["deployment_name"] == "hourly"
    assert http.post(f"/v1/admin/deployments/{uuid.uuid4()}/pause").status_code == 404


def test_runs_leave_out_the_ones_not_started(http: TestClient, prefect: FakePrefect) -> None:
    hourly = _hourly(prefect)
    prefect.add_run(hourly, "COMPLETED", "2026-11-05T10:07:00Z")
    prefect.add_run(hourly, "SCHEDULED", "2026-11-05T13:07:00Z")
    assert [r["state_type"] for r in http.get("/v1/admin/runs").json()] == ["COMPLETED"]
    both = http.get("/v1/admin/runs", params={"scheduled": "true"}).json()
    assert [r["state_type"] for r in both] == ["SCHEDULED", "COMPLETED"]
    failed = http.get("/v1/admin/runs", params={"state": "FAILED"}).json()
    assert failed == []


def test_a_run_with_its_log_and_cancelling_it(http: TestClient, prefect: FakePrefect) -> None:
    run_id = prefect.add_run(_hourly(prefect), "RUNNING", "2026-11-05T12:07:00Z")
    prefect.logs[run_id] = [
        {"timestamp": f"2026-11-05T12:07:0{i}Z", "level": level, "message": f"line {i}"}
        for i, level in enumerate((20, 30, 40))
    ]
    detail = http.get(f"/v1/admin/runs/{run_id}").json()
    assert detail["flow_name"] == "blt-collect-jobs"
    assert [(line["level"], line["message"]) for line in detail["logs"]] == [
        ("INFO", "line 0"),
        ("WARNING", "line 1"),
        ("ERROR", "line 2"),
    ]
    newer = http.get(f"/v1/admin/runs/{run_id}", params={"log_offset": 2}).json()
    assert [line["message"] for line in newer["logs"]] == ["line 2"]

    cancelled = http.post(f"/v1/admin/runs/{run_id}/cancel").json()
    assert cancelled["state_type"] == "CANCELLING"
    assert http.get(f"/v1/admin/runs/{uuid.uuid4()}").status_code == 404


# --- when Prefect is not there -------------------------------------------


def test_without_prefect_the_admin_pages_say_so() -> None:
    with TestClient(create_app(_settings()), headers={"X-API-Key": KEY}) as client:
        response = client.get("/v1/admin/sources")
    assert response.status_code == 503
    assert "BLT_PREFECT_API_URL" in response.json()["detail"]


def test_prefect_down_is_a_503_and_its_errors_a_502(prefect: FakePrefect) -> None:
    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    def broken(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    for handler, code in ((down, 503), (broken, 502)):
        api = PrefectApi(
            httpx.Client(base_url="http://p/api/", transport=httpx.MockTransport(handler))
        )
        with TestClient(create_app(_settings(), prefect=api), headers={"X-API-Key": KEY}) as client:
            assert client.get("/v1/admin/deployments").status_code == code


def test_admin_needs_the_api_key(http: TestClient) -> None:
    assert http.get("/v1/admin/sources", headers={"X-API-Key": "wrong"}).status_code == 401


# --- run health --------------------------------------------------------------


def _ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def _source_run(prefect: FakePrefect, state: str, minutes: int) -> str:
    deployment = next(iter(prefect.deployments))
    run_id = prefect.add_run(deployment, state, _ago(minutes), parameters={"primary": "nbu01"})
    prefect.runs[run_id]["state"]["timestamp"] = _ago(minutes)
    return run_id


def test_run_health_is_healthy_with_nothing_running(http: TestClient, prefect: FakePrefect) -> None:
    health = http.get("/v1/admin/runs/health").json()
    assert health["slots"] is None and health["healthy"] is True
    assert health["stuck"] == [] and health["leaked_slots"] == 0

    prefect.slot_limit = {"limit": 4, "active_slots": 0}
    assert http.get("/v1/admin/runs/health").json()["slots"] == {
        "name": "blt-commcell-runs",
        "limit": 4,
        "active": 0,
    }


def test_runs_queued_behind_lost_slots_are_stuck_and_clearing_frees_them(
    http: TestClient, prefect: FakePrefect
) -> None:
    # The desktop on 2026-11-09: the Prefect server restarted while runs
    # held every slot, and each run since waits for one, RUNNING.
    prefect.slot_limit = {"limit": 4, "active_slots": 4}
    waiting = [_source_run(prefect, "RUNNING", 300 + i) for i in range(6)]
    fresh = _source_run(prefect, "RUNNING", 5)
    pending = _source_run(prefect, "PENDING", 30)
    deployment = next(iter(prefect.deployments))
    prefect.add_run(deployment, "RUNNING", _ago(400))  # no source: takes no slot

    health = http.get("/v1/admin/runs/health").json()
    assert health["healthy"] is False
    assert health["collecting"] == 7  # the source runs RUNNING, waiters among them
    stuck = {run["id"]: run for run in health["stuck"]}
    assert set(waiting) | {pending} <= set(stuck) and fresh not in stuck
    assert stuck[waiting[5]]["stuck_minutes"] >= 305
    assert health["stuck"][0]["stuck_minutes"] >= health["stuck"][-1]["stuck_minutes"]

    cleared = http.post(
        "/v1/admin/runs/clear-stuck", json={"run_ids": [*waiting, pending, fresh, _id()]}
    ).json()
    assert set(cleared["cancelled"]) == set(waiting) | {pending}
    assert cleared["skipped"] == 2  # still fresh, and not there at all
    assert prefect.runs[fresh]["state"]["type"] == "RUNNING"
    assert all(prefect.runs[r]["state"]["type"] == "CANCELLED" for r in waiting)
    # One source run is left collecting: the count comes down to it, no lower.
    assert (cleared["slots_before"], cleared["slots_after"]) == (4, 1)
    assert prefect.slot_limit["active_slots"] == 1
    forced = [b for m, p, b in prefect.calls if p.endswith("/set_state")]
    assert forced and all(b["force"] is True for b in forced)


def test_slots_held_by_no_run_are_leaked_and_cleared_without_cancelling(
    http: TestClient, prefect: FakePrefect
) -> None:
    prefect.slot_limit = {"limit": 4, "active_slots": 3}
    _source_run(prefect, "RUNNING", 2)
    health = http.get("/v1/admin/runs/health").json()
    assert (health["collecting"], health["leaked_slots"], health["healthy"]) == (1, 2, False)

    cleared = http.post("/v1/admin/runs/clear-stuck", json={}).json()
    assert cleared == {"cancelled": [], "skipped": 0, "slots_before": 3, "slots_after": 1}


def test_a_run_is_stuck_after_the_minutes_asked_for(http: TestClient, prefect: FakePrefect) -> None:
    run_id = _source_run(prefect, "RUNNING", 45)
    assert http.get("/v1/admin/runs/health").json()["stuck"] == []
    stuck = http.get("/v1/admin/runs/health", params={"stuck_after_minutes": 30}).json()["stuck"]
    assert [r["id"] for r in stuck] == [run_id]
    assert http.get("/v1/admin/runs/health", params={"stuck_after_minutes": 1}).status_code == 422
    # Cleared by the rule it was listed under, not the default.
    assert (
        http.post("/v1/admin/runs/clear-stuck", json={"run_ids": [run_id]}).json()["cancelled"]
        == []
    )
    assert http.post(
        "/v1/admin/runs/clear-stuck", json={"run_ids": [run_id], "stuck_after_minutes": 30}
    ).json()["cancelled"] == [run_id]


# --- deploy/maintenance.sh (blt.api.maintenance) ------------------------------


@pytest.fixture
def maintenance(prefect: FakePrefect, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from blt.api import maintenance as module

    api = PrefectApi(
        httpx.Client(
            base_url="http://prefect/api/",
            transport=httpx.MockTransport(prefect.handle),
            auth=httpx.BasicAuth("admin", "pw"),
        )
    )
    app = create_app(_settings(), prefect=api)
    monkeypatch.setattr(
        module,
        "api_client",
        lambda: TestClient(app, base_url="http://testserver/v1/admin/", headers={"X-API-Key": KEY}),
    )
    yield module


def test_maintenance_status_says_whether_runs_get_through(
    maintenance: Any, prefect: FakePrefect, capsys: pytest.CaptureFixture[str]
) -> None:
    prefect.slot_limit = {"limit": 4, "active_slots": 0}
    assert maintenance.main(["status"]) == 0
    assert "Runs are getting through." in capsys.readouterr().out

    prefect.slot_limit["active_slots"] = 4
    stuck = _source_run(prefect, "RUNNING", 300)
    assert maintenance.main(["status"]) == 1
    out = capsys.readouterr().out
    assert "4 of 4 taken; 1 runs collecting; 3 held by runs that are gone" in out
    assert prefect.runs[stuck]["name"] in out and "Runs are stuck." in out


def test_maintenance_unstick_dry_run_changes_nothing_then_clears(
    maintenance: Any, prefect: FakePrefect, capsys: pytest.CaptureFixture[str]
) -> None:
    prefect.slot_limit = {"limit": 4, "active_slots": 4}
    stuck = [_source_run(prefect, "RUNNING", 300) for _ in range(2)]
    assert maintenance.main(["unstick", "--dry-run"]) == 0
    assert "Would cancel 2 runs" in capsys.readouterr().out
    assert all(prefect.runs[r]["state"]["type"] == "RUNNING" for r in stuck)
    assert prefect.slot_limit["active_slots"] == 4

    assert maintenance.main(["unstick"]) == 0
    assert "Cancelled 2 runs (0 had moved on); slots taken 4 -> 0." in capsys.readouterr().out
    assert all(prefect.runs[r]["state"]["type"] == "CANCELLED" for r in stuck)
    assert maintenance.main(["unstick"]) == 0
    assert "Nothing to clear." in capsys.readouterr().out
