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
    assert {d["name"] for d in deployments} == {"hourly", "twice-daily", "by-hand"}
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
    assert list(found) == ["postgres", "api", "prefect-server", "prefect-worker"]
    ports = {name: c.get("ports") for name, c in found.items()}
    assert ports["api"] == [{"containerPort": 8000, "hostPort": 8188, "hostIP": "127.0.0.1"}]
    assert ports["postgres"] == [{"containerPort": 5432, "hostPort": 5544, "hostIP": "127.0.0.1"}]
    # The UI may be opened to other machines without the API or database.
    assert ports["prefect-server"] == [
        {"containerPort": 4200, "hostPort": 4200, "hostIP": "0.0.0.0"}
    ]
    worker_env = {e["name"]: e.get("value") for e in found["prefect-worker"]["env"]}
    assert worker_env["PREFECT_API_URL"] == "http://127.0.0.1:4200/api"
    assert worker_env["BLT_API_URL"] == "http://127.0.0.1:8000"


def test_secrets_are_only_in_the_secret(tmp_path: Path) -> None:
    documents = render(tmp_path)
    secret = documents[0]["data"]
    decoded = {k: base64.b64decode(v).decode() for k, v in secret.items()}
    assert decoded["PREFECT_AUTH"] == "admin:ui-secret"
    assert decoded["BLT_DATABASE_URL"] == ("postgresql+psycopg://blt:pg-secret@127.0.0.1:5432/blt")
    rest = yaml.safe_dump(documents[1:])
    for value in ("pg-secret", "prefect-secret", "ui-secret", "key-1"):
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


def test_with_prefect_off_the_pod_has_only_postgres_and_the_api(tmp_path: Path) -> None:
    found = containers(render(tmp_path, "BLT_PREFECT=false\n"))
    assert list(found) == ["postgres", "api"]


@pytest.mark.parametrize("bad", ["PREFECT_AUTH=no-colon\n", "BLT_PREFECT_BIND_ADDRESS=lan\n"])
def test_bad_prefect_settings_are_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(AssertionError):
        render(tmp_path, bad)
