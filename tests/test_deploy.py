"""The deployment files: versions that must agree, and the Kubernetes
manifest as deploy/kube/up.sh renders it."""

from __future__ import annotations

import base64
import importlib
import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent

ENV = """\
BLT_API_KEY=key-1
POSTGRES_PASSWORD=pg-secret
BLT_API_PORT=8188
BLT_PG_PORT=5544
BLT_PREFECT=true
PREFECT_DB_PASSWORD=prefect-secret
PREFECT_AUTH=admin:ui-secret
BLT_PREFECT_BIND_ADDRESS=0.0.0.0
BLT_UI_PASSWORD_HASH=scrypt:1024:8:1:c2FsdA:aGFzaC1zZWNyZXQ
BLT_UI_SESSION_SECRET=session-secret-session-secret-xyz
"""


def locked_prefect_version() -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    return next(p["version"] for p in lock["package"] if p["name"] == "prefect")


def test_server_worker_and_deployments_use_one_prefect_version() -> None:
    version = locked_prefect_version()
    server = (ROOT / "deploy/prefect/server/Containerfile").read_text()
    assert re.search(rf"prefecthq/prefect:{re.escape(version)}-", server), server
    assert yaml.safe_load((ROOT / "prefect.yaml").read_text())["prefect-version"] == version


def test_every_deployment_points_at_a_flow_that_exists() -> None:
    deployments = yaml.safe_load((ROOT / "prefect.yaml").read_text())["deployments"]
    assert sorted(d["entrypoint"].split(":")[1] + "/" + d["name"] for d in deployments) == [
        "backfill/by-hand",
        "collect_commcell/per-commcell",
        "collect_inventory/twice-daily",
        "collect_jobs/hourly",
        "collect_storage/hourly",
        "inventory_commcell/per-commcell",
        "netbackup_check/by-hand",
        "netbackup_collect_storage/hourly",
        "netbackup_fetch_ca/by-hand",
        "netbackup_storage_primary/per-primary",
        "rubrik_collect_account/per-account",
        "rubrik_collect_inventory/twice-daily",
        "rubrik_collect_jobs/hourly",
        "rubrik_inventory_account/per-account",
        "storage_commcell/per-commcell",
    ]
    for deployment in deployments:
        module, name = deployment["entrypoint"].split(":")
        target = getattr(importlib.import_module(module), name)
        assert hasattr(target, "deploy"), f"{deployment['entrypoint']} is not a flow"


def clean_environment() -> dict[str, str]:
    """This process's environment without blt's own settings, so a
    developer's real blt.env, if loaded, cannot leak into the render."""
    prefixes = ("BLT_", "PREFECT_", "POSTGRES_")
    return {k: v for k, v in os.environ.items() if not k.startswith(prefixes)}


def render(tmp_path: Path, extra: str = "") -> list[dict]:
    env_file = tmp_path / "blt.env"
    env_file.write_text(ENV + extra)
    result = subprocess.run(
        ["bash", "deploy/kube/up.sh", "--render-with-secrets"],
        cwd=ROOT,
        env={**clean_environment(), "BLT_ENV_FILE": str(env_file)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return list(yaml.safe_load_all(result.stdout))


def containers(documents: list[dict]) -> dict[str, dict]:
    pod = next(d for d in documents if d["kind"] == "Pod")
    return {c["name"]: c for c in pod["spec"]["containers"]}


def test_the_manifest_publishes_each_service_on_its_own_address(tmp_path: Path) -> None:
    documents = render(tmp_path)
    assert [d["kind"] for d in documents] == ["Secret", "PersistentVolumeClaim", "Pod"]
    found = containers(documents)
    assert list(found) == ["postgres", "api", "ui", "prefect-server", "prefect-worker"]
    ports = {name: c.get("ports") for name, c in found.items()}
    assert ports["api"] == [{"containerPort": 8000, "hostPort": 8188, "hostIP": "127.0.0.1"}]
    assert ports["postgres"] == [{"containerPort": 5432, "hostPort": 5544, "hostIP": "127.0.0.1"}]
    # Prefect may be opened to other machines without the API or database.
    assert ports["prefect-server"] == [
        {"containerPort": 4200, "hostPort": 4200, "hostIP": "0.0.0.0"}
    ]
    # The web UI stays on this machine unless asked, whatever else is open.
    assert ports["ui"] == [{"containerPort": 3000, "hostPort": 8090, "hostIP": "127.0.0.1"}]
    ui_env = {e["name"]: e.get("value") for e in found["ui"]["env"]}
    assert ui_env["BLT_API_URL"] == "http://127.0.0.1:8000"
    assert (ui_env["BLT_UI_SIGN_IN"], ui_env["BLT_UI_USER"]) == ("on", "admin")
    api_env = {e["name"]: e.get("value") for e in found["api"]["env"]}
    assert api_env["BLT_PREFECT_API_URL"] == "http://127.0.0.1:4200/api"
    server_env = {e["name"]: e.get("value") for e in found["prefect-server"]["env"]}
    assert server_env["PREFECT_SERVER_UI_ENABLED"] == "false"
    worker_env = {e["name"]: e.get("value") for e in found["prefect-worker"]["env"]}
    assert worker_env["PREFECT_API_URL"] == "http://127.0.0.1:4200/api"
    assert worker_env["BLT_API_URL"] == "http://127.0.0.1:8000"
    assert worker_env["BLT_MAX_PARALLEL"] == "4"


def test_secrets_are_only_in_the_secret(tmp_path: Path) -> None:
    documents = render(tmp_path)
    secret = documents[0]["data"]
    decoded = {k: base64.b64decode(v).decode() for k, v in secret.items()}
    assert decoded["PREFECT_AUTH"] == "admin:ui-secret"
    assert decoded["BLT_DATABASE_URL"] == ("postgresql+psycopg://blt:pg-secret@127.0.0.1:5432/blt")
    rest = yaml.safe_dump(documents[1:])
    assert decoded["BLT_UI_SESSION_SECRET"] == "session-secret-session-secret-xyz"
    for value in (
        "pg-secret",
        "prefect-secret",
        "ui-secret",
        "key-1",
        "hash-secret",
        "session-secret",
    ):
        assert value not in rest


def test_on_the_host_network_nothing_is_published_and_services_listen_themselves(
    tmp_path: Path,
) -> None:
    found = containers(render(tmp_path, "BLT_POD_NETWORK=host\n"))
    assert all("ports" not in c for c in found.values())
    server_env = {e["name"]: e.get("value") for e in found["prefect-server"]["env"]}
    assert (server_env["PREFECT_LISTEN_HOST"], server_env["PREFECT_LISTEN_PORT"]) == (
        "0.0.0.0",
        "4200",
    )
    api_env = {e["name"]: e.get("value") for e in found["api"]["env"]}
    assert (api_env["BLT_LISTEN_HOST"], api_env["BLT_LISTEN_PORT"]) == ("127.0.0.1", "8188")
    ui_env = {e["name"]: e.get("value") for e in found["ui"]["env"]}
    assert (ui_env["HOST"], ui_env["PORT"]) == ("127.0.0.1", "8090")
    assert ui_env["BLT_API_URL"] == "http://127.0.0.1:8188"


def test_with_prefect_off_the_pod_has_only_postgres_the_api_and_the_ui(tmp_path: Path) -> None:
    found = containers(render(tmp_path, "BLT_PREFECT=false\n"))
    assert list(found) == ["postgres", "api", "ui"]
    api_env = {e["name"]: e.get("value") for e in found["api"]["env"]}
    assert api_env["BLT_PREFECT_API_URL"] == ""
    found = containers(render(tmp_path, "BLT_PREFECT=false\nBLT_UI=false\n"))
    assert list(found) == ["postgres", "api"]


def test_the_ui_stays_local_when_the_rest_is_opened(tmp_path: Path) -> None:
    found = containers(render(tmp_path, "BLT_BIND_ADDRESS=0.0.0.0\n"))
    assert found["api"]["ports"][0]["hostIP"] == "0.0.0.0"
    assert found["ui"]["ports"][0]["hostIP"] == "127.0.0.1"
    # ... unless it is asked for, on its own setting.
    found = containers(render(tmp_path, "BLT_UI_BIND_ADDRESS=0.0.0.0\nBLT_UI_PORT=9000\n"))
    assert found["ui"]["ports"] == [{"containerPort": 3000, "hostPort": 9000, "hostIP": "0.0.0.0"}]


def test_prefect_stays_on_this_machine_unless_asked(tmp_path: Path) -> None:
    found = containers(render(tmp_path, "BLT_PREFECT_BIND_ADDRESS=\nBLT_BIND_ADDRESS=0.0.0.0\n"))
    assert found["prefect-server"]["ports"][0]["hostIP"] == "127.0.0.1"


@pytest.mark.parametrize(
    "bad",
    [
        "BLT_UI_PASSWORD_HASH=\n",
        "BLT_UI_PASSWORD_HASH=hunter2\n",
        "BLT_UI_SESSION_SECRET=short\n",
        "BLT_UI_SIGN_IN=off\nBLT_UI_BIND_ADDRESS=0.0.0.0\n",
    ],
)
def test_the_ui_needs_its_sign_in_settings(tmp_path: Path, bad: str) -> None:
    with pytest.raises(AssertionError):
        render(tmp_path, bad)


def test_sign_in_can_be_off_for_a_ui_on_this_machine_only(tmp_path: Path) -> None:
    found = containers(render(tmp_path, "BLT_UI_SIGN_IN=off\nBLT_UI_PASSWORD_HASH=\n"))
    ui_env = {e["name"]: e.get("value") for e in found["ui"]["env"]}
    assert ui_env["BLT_UI_SIGN_IN"] == "off"


def test_a_bad_ui_address_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AssertionError):
        render(tmp_path, "BLT_UI_BIND_ADDRESS=everywhere\n")


@pytest.mark.parametrize(
    "bad",
    ["PREFECT_AUTH=no-colon\n", "BLT_PREFECT_BIND_ADDRESS=lan\n", "BLT_MAX_PARALLEL=lots\n"],
)
def test_bad_prefect_settings_are_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(AssertionError):
        render(tmp_path, bad)


def test_git_bash_is_told_not_to_rewrite_podman_arguments() -> None:
    # Otherwise an argument such as /api reached the container as
    # C:/Program Files/Git/api (seen with PREFECT_SERVER_UI_API_URL on
    # Windows with podman in WSL).
    script = (ROOT / "deploy/env.sh").read_text()
    assert "export MSYS_NO_PATHCONV=1" in script
    assert script.index("MSYS_NO_PATHCONV") < script.index("build_images()")


def test_the_parents_start_deployments_that_exist() -> None:
    from blt.flows import flows

    deployments = yaml.safe_load((ROOT / "prefect.yaml").read_text())["deployments"]
    by_flow = {
        getattr(
            importlib.import_module(d["entrypoint"].split(":")[0]), d["entrypoint"].split(":")[1]
        ).name
        + "/"
        + d["name"]
        for d in deployments
    }
    assert {flows.COLLECT_ONE, flows.INVENTORY_ONE} <= by_flow
