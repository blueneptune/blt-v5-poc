"""Inventory and validation: what a source says exists, stored and then
judged - collector -> API -> Postgres, with Commvault faked."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
import respx
from fastapi.testclient import TestClient
from sdk_primer import APIClient

from blt.collector.collect import run_collection, run_inventory
from blt.collector.store import BltStore
from blt.commvault.client import CommvaultClient
from blt.commvault.inventory import iter_inventory
from blt.schemas import JobIn, ObjectIn

BASE = "https://cs.test/commandcenter/api"


def _now() -> datetime:
    return datetime.now(UTC)


def _client(key: str, name: str) -> ObjectIn:
    return ObjectIn(kind="client", source_key=key, name=name)


def _instance(
    key: str,
    name: str,
    client: str,
    app: str = "SQL Server",
    full_job: tuple[int, int, int] | None = None,
) -> ObjectIn:
    """`full_job` is (job id, databases the job says it took, skipped)."""
    attributes = {}
    if full_job:
        attributes["last_full_job"] = dict(
            zip(("job_id", "backed_up", "skipped"), full_job, strict=True)
        )
    return ObjectIn(
        kind="instance",
        source_key=key,
        name=name,
        parent_kind="client",
        parent_key=client,
        app_type=app,
        attributes=attributes,
    )


def _database(
    instance: str,
    name: str,
    backed_up: datetime | None = None,
    job: int | None = None,
    full: int | None = None,
) -> ObjectIn:
    return ObjectIn(
        kind="database",
        source_key=f"{instance}/{name}",
        name=name,
        parent_kind="instance",
        parent_key=instance,
        app_type="SQL Server",
        last_backup_at=backed_up,
        last_backup_job_id=job,
        last_full_job_id=full,
    )


def _inventory(*levels: list[ObjectIn]):  # noqa: ANN202
    def batches() -> Iterator[list[ObjectIn]]:
        yield from levels

    return batches


def _verdicts(http: TestClient, **params: float) -> dict[str, str]:
    report = http.get("/commcells/prod/validation", params=params).json()
    return {row["name"]: row["verdict"] for row in report["rows"]}


@pytest.fixture
def store(http: TestClient, blt_api: APIClient) -> BltStore:
    store = BltStore(blt_api, "prod")

    # An ordinary collection first: it is what registers the CommCell.
    class NoJobs:
        def iter_pages(self, lookup_seconds: int, *, ended_between=None):  # noqa: ANN001, ANN202
            return iter(())

        def get(self, job_id: int) -> JobIn | None:
            return None

        def oldest_start(self, lookup_seconds: int) -> datetime | None:
            return None

    run_collection(NoJobs(), store)
    return store


def test_every_verdict(http: TestClient, store: BltStore) -> None:
    fresh = _now() - timedelta(hours=1)
    old = _now() - timedelta(hours=30)
    run_inventory(
        _inventory(
            [_client("15", "sql01"), _client("16", "sql02"), _client("17", "web01")],
            [
                _instance("6", "sql01", "15"),
                _instance("7", "sql02", "16"),
                _instance("8", "DefaultInstanceName", "17", app="File System"),
            ],
            [
                _database("6", "sales", fresh),
                _database("6", "hr", old),
                _database("6", "new_and_never_backed_up"),
                _database("6", "master"),
            ],
        ),
        store,
    )

    assert _verdicts(http) == {
        "sales": "ok",
        "hr": "stale",
        "new_and_never_backed_up": "unprotected",
        # In backup content, but the source gives no backup time for it.
        "master": "unverified",
        # A SQL instance with nothing known under it is itself a finding;
        # a file system instance with no databases is not.
        "sql02": "empty",
    }
    report = http.get("/commcells/prod/validation").json()
    assert report["problems"] == 3  # stale + unprotected + empty
    assert report["sources"] == ["commvault"]
    assert report["inventory_at"] is not None
    row = next(r for r in report["rows"] if r["name"] == "hr")
    assert (row["client"], row["instance"]) == ("sql01", "sql01")

    # "Stale" is a matter of the limit asked for.
    assert _verdicts(http, max_age_hours=48)["hr"] == "ok"


def test_a_complete_inventory_marks_what_it_did_not_see_as_gone(
    http: TestClient, store: BltStore
) -> None:
    fresh = _now() - timedelta(hours=1)
    clients, instances = [_client("15", "sql01")], [_instance("6", "sql01", "15")]
    run_inventory(
        _inventory(
            clients, instances, [_database("6", "sales", fresh), _database("6", "tmp", fresh)]
        ),
        store,
    )
    first_seen = {o["name"]: o["first_seen_at"] for o in http.get("/commcells/prod/objects").json()}

    # Next inventory: "tmp" has been dropped.
    run_inventory(_inventory(clients, instances, [_database("6", "sales", fresh)]), store)

    assert _verdicts(http) == {"sales": "ok", "tmp": "gone"}
    objects = {o["name"]: o for o in http.get("/commcells/prod/objects").json()}
    assert objects["tmp"]["present"] is False
    # Same rows, updated in place.
    assert objects["sales"]["first_seen_at"] == first_seen["sales"]
    assert objects["sales"]["present"] is True

    # And if it comes back, it is present again.
    run_inventory(
        _inventory(
            clients, instances, [_database("6", "sales", fresh), _database("6", "tmp", fresh)]
        ),
        store,
    )
    assert _verdicts(http) == {"sales": "ok", "tmp": "ok"}


def test_a_failed_inventory_marks_nothing_gone(http: TestClient, store: BltStore) -> None:
    fresh = _now() - timedelta(hours=1)
    clients, instances = [_client("15", "sql01")], [_instance("6", "sql01", "15")]
    run_inventory(_inventory(clients, instances, [_database("6", "sales", fresh)]), store)

    def breaks_after_clients() -> Iterator[list[ObjectIn]]:
        yield clients
        raise RuntimeError("CommServe went away")

    with pytest.raises(RuntimeError):
        run_inventory(breaks_after_clients, store)

    # It saw less because it stopped, not because the database vanished.
    assert _verdicts(http) == {"sales": "ok"}
    # And an inventory run never moves the job watermark.
    last = http.get("/commcells/prod/last-run").json()
    assert last["last_run"]["mode"] == "inventory"
    assert last["last_run"]["status"] == "failed"


def test_children_need_their_parents_first(http: TestClient, store: BltStore) -> None:
    run = http.post(
        "/commcells/prod/runs",
        json={"mode": "inventory", "started_at": _now().isoformat(), "lookup_seconds": 0},
    ).json()
    body = {
        "run_id": run["id"],
        "collected_at": _now().isoformat(),
        "objects": [_database("99", "orphan").model_dump(mode="json")],
    }
    response = http.post("/commcells/prod/objects", json=body)
    assert response.status_code == 422
    assert "send parents first" in response.text


def test_validation_shows_the_status_of_the_last_backup_job(
    http: TestClient, store: BltStore, blt_api: APIClient
) -> None:
    # blt already collected job 116 as Completed...
    http.post(
        "/commcells/prod/jobs",
        json={
            "collected_at": _now().isoformat(),
            "jobs": [JobIn(job_id=116, status="Completed").model_dump(mode="json")],
        },
    )
    fresh = _now() - timedelta(hours=1)
    run_inventory(
        _inventory(
            [_client("15", "sql01")],
            [_instance("6", "sql01", "15")],
            [_database("6", "sales", fresh, job=116), _database("6", "hr", fresh, job=999)],
        ),
        store,
    )
    rows = {r["name"]: r for r in http.get("/commcells/prod/validation").json()["rows"]}
    assert rows["sales"]["last_backup_job_status"] == "Completed"
    # ...and has never seen job 999.
    assert rows["hr"]["last_backup_job_status"] is None


@respx.mock
def test_commvault_inventory_separates_existing_from_backed_up() -> None:
    respx.post(f"{BASE}/Login").respond(json={"token": "QSDK abc"})
    respx.get(f"{BASE}/Client").respond(
        json={
            "clientProperties": [
                {
                    "client": {
                        "clientEntity": {"clientId": 15, "clientName": "sql01", "hostName": "sql01"}
                    }
                },
                {"client": {"clientEntity": {"clientId": 2, "clientName": "cs", "hostName": "cs"}}},
            ]
        }
    )
    respx.get(f"{BASE}/Agent", params={"clientId": 15}).respond(
        json={"agentProperties": [{"idaEntity": {"appName": "SQL Server"}}]}
    )
    respx.get(f"{BASE}/Agent", params={"clientId": 2}).respond(json={})
    respx.get(f"{BASE}/Instance", params={"clientId": 15}).respond(
        json={
            "instanceProperties": [
                {
                    "instance": {
                        "instanceId": 6,
                        "instanceName": "sql01",
                        "clientId": 15,
                        "appName": "SQL Server",
                        "applicationId": 81,
                    }
                },
                {
                    "instance": {
                        "instanceId": 1,
                        "instanceName": "DefaultInstanceName",
                        "clientId": 15,
                        "appName": "File System",
                        "applicationId": 33,
                    }
                },
            ]
        }
    )
    # Every client's file system agent has the same instance id, 1.
    respx.get(f"{BASE}/Instance", params={"clientId": 2}).respond(
        json={
            "instanceProperties": [
                {
                    "instance": {
                        "instanceId": 1,
                        "instanceName": "DefaultInstanceName",
                        "clientId": 2,
                        "appName": "File System",
                        "applicationId": 33,
                    }
                }
            ]
        }
    )
    respx.get(f"{BASE}/Subclient", params={"clientId": 15}).respond(
        json={
            "subClientProperties": [
                {
                    "subClientEntity": {
                        "subclientId": 43,
                        "subclientName": "default",
                        "instanceId": 6,
                    }
                }
            ]
        }
    )
    respx.get(f"{BASE}/Subclient/43").respond(
        json={
            "subClientProperties": [
                {
                    "planEntity": {"planName": "Standard Plan"},
                    "content": [
                        {"mssqlDbContent": {"databaseName": "master", "discoverType": 1}},
                        {"mssqlDbContent": {"databaseName": "sales", "discoverType": 1}},
                        {"mssqlDbContent": {"databaseName": "offline_db", "discoverType": 1}},
                    ],
                }
            ]
        }
    )
    respx.get(f"{BASE}/sql/databases", params={"instance": 6}).respond(
        json={
            "SqlDatabase": [
                {"dbName": "sales", "bkpTime": 1_791_325_681, "jobId": 116, "fullJobId": 112}
            ]
        }
    )
    details = respx.post(f"{BASE}/JobDetails").respond(
        json={"job": {"jobDetail": {"detailInfo": {"numOfObjects": 2, "skippedItems": 1}}}}
    )

    with CommvaultClient(BASE, "svc", "pw") as commvault:
        clients, instances, databases = list(iter_inventory(commvault))

    assert [(c.source_key, c.name) for c in clients] == [("15", "sql01"), ("2", "cs")]
    assert clients[0].attributes["agents"] == ["SQL Server"]
    # Instance ids repeat across clients, so the key carries client and agent.
    assert [(i.source_key, i.parent_key, i.app_type) for i in instances] == [
        ("15/81/6", "15", "SQL Server"),
        ("15/33/1", "15", "File System"),
        ("2/33/1", "2", "File System"),
    ]
    assert len({i.source_key for i in instances}) == 3
    by_name = {d.name: d for d in databases}
    # Exists: everything in subclient content. Backed up: only what
    # /sql/databases lists.
    assert set(by_name) == {"master", "sales", "offline_db"}
    assert by_name["sales"].last_backup_at is not None
    assert by_name["sales"].last_backup_job_id == 116
    assert by_name["offline_db"].last_backup_at is None
    assert by_name["sales"].parent_key == "15/81/6"
    assert by_name["sales"].source_key == "15/81/6/sales"
    assert by_name["sales"].attributes["plan"] == "Standard Plan"
    assert by_name["sales"].last_full_job_id == 112
    # The instance carries what the CommServe says its latest full took.
    assert json.loads(details.calls[0].request.content) == {"jobId": 112}
    assert instances[0].attributes["last_full_job"] == {
        "job_id": 112,
        "backed_up": 2,
        "skipped": 1,
    }
    assert instances[1].attributes["last_full_job"] is None


# --- The per-instance report: discovered vs backed up by the latest full.


def _report(http: TestClient, **params: str) -> dict[str, dict]:
    rows = http.get("/commcells/prod/instance-report", params=params).json()
    return {row["instance"]: row for row in rows}


def _collect_job(http: TestClient, job_id: int, status: str = "Completed") -> None:
    http.post(
        "/commcells/prod/jobs",
        json={
            "collected_at": _now().isoformat(),
            "jobs": [JobIn(job_id=job_id, status=status).model_dump(mode="json")],
        },
    )


def test_report_full_backup_takes_everything(http: TestClient, store: BltStore) -> None:
    """Test 1: a full backup of every database."""
    _collect_job(http, 112)
    t = _now() - timedelta(hours=1)
    run_inventory(
        _inventory(
            [_client("15", "sql01")],
            [_instance("6", "sql01", "15", full_job=(112, 4, 0))],
            [
                _database("6", "master"),
                _database("6", "sales", t, job=116, full=112),
                _database("6", "hr", t, job=116, full=112),
                _database("6", "ops", t, job=116, full=112),
            ],
        ),
        store,
    )
    row = _report(http)["sql01"]
    assert (row["client"], row["job_id"], row["job_status"]) == ("sql01", 112, "Completed")
    assert (row["discovered"], row["backed_up"]) == (4, 4)
    assert row["missed"] == []
    # master has no per-database job, but the job's own total covers it.
    assert row["unconfirmed"] == []
    assert row["notes"] == "all discovered databases backed up"


def test_report_partial_backup_names_what_was_missed(http: TestClient, store: BltStore) -> None:
    """Test 2: the latest full took some databases and not others."""
    _collect_job(http, 120, "Completed w/ one or more errors")
    t = _now() - timedelta(hours=1)
    run_inventory(
        _inventory(
            [_client("15", "sql01")],
            [_instance("6", "sql01", "15", full_job=(120, 3, 0))],
            [
                _database("6", "master"),
                _database("6", "sales", t, job=120, full=120),
                _database("6", "ops", t, job=120, full=120),
                # Last fully backed up by an earlier job: this one missed it.
                _database("6", "hr", t - timedelta(days=1), job=112, full=112),
            ],
        ),
        store,
    )
    row = _report(http)["sql01"]
    assert (row["discovered"], row["backed_up"]) == (4, 3)
    assert row["missed"] == ["hr"]
    assert "missed: hr" in row["notes"]
    assert "job ended Completed w/ one or more errors" in row["notes"]


def test_report_cannot_see_databases_added_since_the_backup(
    http: TestClient, store: BltStore
) -> None:
    """Test 3: a database created after the full. The source has not
    seen it, so the inventory - and so the report - is unchanged until
    the next backup discovers it."""
    _collect_job(http, 112)
    t = _now() - timedelta(hours=1)
    clients = [_client("15", "sql01")]
    before = [_database("6", "sales", t, job=112, full=112)]
    run_inventory(
        _inventory(clients, [_instance("6", "sql01", "15", full_job=(112, 1, 0))], before), store
    )
    # ... a database is created on the server; Commvault's listing is the same ...
    run_inventory(
        _inventory(clients, [_instance("6", "sql01", "15", full_job=(112, 1, 0))], before), store
    )
    row = _report(http)["sql01"]
    assert (row["discovered"], row["backed_up"], row["missed"]) == (1, 1, [])

    # The next full discovers it and takes it.
    _collect_job(http, 130)
    after = [
        _database("6", "sales", t, job=130, full=130),
        _database("6", "brand_new", t, job=130, full=130),
    ]
    run_inventory(
        _inventory(clients, [_instance("6", "sql01", "15", full_job=(130, 2, 0))], after), store
    )
    row = _report(http)["sql01"]
    assert (row["job_id"], row["discovered"], row["backed_up"]) == (130, 2, 2)

    # History keeps one row per full backup job.
    history = http.get("/commcells/prod/instance-report", params={"history": "true"}).json()
    assert [(r["job_id"], r["discovered"], r["backed_up"]) for r in history] == [
        (112, 1, 1),
        (130, 2, 2),
    ]


def test_report_instance_with_no_sql_backup_still_gets_a_row(
    http: TestClient, store: BltStore
) -> None:
    """Test 4: the SQL agent is installed and the instance known, but no
    SQL backup has ever run - so no databases are known and there is no
    job. It must not simply be absent from a per-backup report."""
    run_inventory(
        _inventory(
            [_client("16", "sql02")],
            [
                _instance("7", "sql02", "16"),
                _instance("9", "DefaultInstanceName", "16", app="File System"),
            ],
            [],
        ),
        store,
    )
    report = _report(http)
    assert list(report) == ["sql02"]  # file system instances are not in this report
    row = report["sql02"]
    assert (row["job_id"], row["discovered"], row["backed_up"]) == (None, 0, 0)
    assert "no SQL backup has ever run" in row["notes"]
    history = http.get("/commcells/prod/instance-report", params={"history": "true"}).json()
    assert [(r["instance"], r["job_id"]) for r in history] == [("sql02", None)]


@respx.mock
def test_one_unreadable_client_is_skipped_not_fatal(http: TestClient, store: BltStore) -> None:
    respx.post(f"{BASE}/Login").respond(json={"token": "QSDK abc"})
    respx.get(f"{BASE}/Client").respond(
        json={
            "clientProperties": [
                {"client": {"clientEntity": {"clientId": 15, "clientName": "good"}}},
                {"client": {"clientEntity": {"clientId": 16, "clientName": "broken"}}},
            ]
        }
    )
    respx.get(f"{BASE}/Agent", params={"clientId": 15}).respond(json={})
    respx.get(f"{BASE}/Instance", params={"clientId": 15}).respond(json={})
    respx.get(f"{BASE}/Agent", params={"clientId": 16}).respond(500)

    # Something stored earlier that this inventory will not see.
    run_inventory(_inventory([_client("99", "old")]), store)

    skipped: list[str] = []
    with CommvaultClient(BASE, "svc", "pw") as commvault:
        run = run_inventory(lambda: iter_inventory(commvault, skipped), store, problems=skipped)

    assert len(skipped) == 1 and skipped[0].startswith("client broken:")
    # What could be read is stored...
    objects = {o["name"]: o for o in http.get("/commcells/prod/objects").json()}
    assert {"good", "broken", "old"} <= set(objects)
    # ...but an inventory with a hole in it is not a complete one, so it
    # is recorded as failed and nothing is declared gone.
    assert run.status == "failed" and "1 could not be read" in (run.error or "")
    assert objects["old"]["present"] is True
