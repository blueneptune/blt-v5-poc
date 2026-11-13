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
        "commvault_check/by-hand",
        "inventory_commcell/per-commcell",
        "netbackup_check/by-hand",
        "netbackup_collect_jobs/every-15-minutes",
        "netbackup_collect_storage/hourly",
        "netbackup_fetch_ca/by-hand",
        "netbackup_jobs_primary/per-primary",
        "netbackup_storage_primary/per-primary",
        "rubrik_check/by-hand",
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
    kinds = [(d["kind"], d["metadata"]["name"]) for d in documents]
    assert kinds == [
        ("Secret", "blt-secrets"),
        ("PersistentVolumeClaim", "blt-pgdata"),
        ("PersistentVolumeClaim", "blt-prefect-home"),
        ("Pod", "blt"),
    ]
    found = containers(documents)
    assert list(found) == ["postgres", "api", "ui", "prefect-server", "prefect-worker"]
    # The server's slot leases outlive a restart (deploy/prefect/server/Containerfile).
    volumes = {
        v["name"]: v["persistentVolumeClaim"]["claimName"] for v in documents[-1]["spec"]["volumes"]
    }
    assert volumes["prefect-home"] == "blt-prefect-home"
    assert found["prefect-server"]["volumeMounts"] == [
        {"name": "prefect-home", "mountPath": "/var/lib/prefect"}
    ]
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
    # Without it the UI assumes https and refuses the sign-in post as
    # cross-site (2026.11.06.1 on the work desktop).
    assert ui_env["ORIGIN"] == "http://localhost:8090"
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
    assert ui_origin(found) == "http://localhost:9000"
    found = containers(render(tmp_path, "BLT_UI_ORIGIN=https://blt.example.test\n"))
    assert ui_origin(found) == "https://blt.example.test"


def test_the_api_alone_can_be_opened_while_postgres_stays_local(tmp_path: Path) -> None:
    found = containers(render(tmp_path, "BLT_API_BIND_ADDRESS=0.0.0.0\n"))
    assert found["api"]["ports"][0]["hostIP"] == "0.0.0.0"
    assert found["postgres"]["ports"][0]["hostIP"] == "127.0.0.1"
    # On the host's network the API itself listens there; Postgres does not.
    found = containers(render(tmp_path, "BLT_API_BIND_ADDRESS=0.0.0.0\nBLT_POD_NETWORK=host\n"))
    api_env = {e["name"]: e.get("value") for e in found["api"]["env"]}
    assert api_env["BLT_LISTEN_HOST"] == "0.0.0.0"
    assert "listen_addresses=127.0.0.1" in found["postgres"]["args"]


def ui_origin(found: dict) -> str:
    return {e["name"]: e.get("value") for e in found["ui"]["env"]}["ORIGIN"]


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
    from blt.flows import netbackup

    assert {netbackup.JOBS_ONE, netbackup.STORAGE_ONE} <= by_flow


# --- remote collectors (deploy/collector.sh, docs/remote-collector.md) ---

FAKE = """#!/usr/bin/env bash
# Records each call - its arguments, one per line, and what came on its
# input - and answers the few questions collector.sh asks.
{{ printf '%s\\n' "$@"; echo "--stdin--"; [[ -t 0 ]] || cat; echo "--end--"; }} >> {log}
case "$*" in
    *"image inspect"*) echo {image_id} ;;
esac
"""


def fake(tmp_path: Path, name: str, image_id: str) -> Path:
    path = tmp_path / name
    path.write_text(FAKE.format(log=tmp_path / f"{name}.log", image_id=image_id))
    path.chmod(0o755)
    return path


def collector(tmp_path: Path, *args: str, remote_image: str = "sha-old") -> str:
    env_file = tmp_path / "blt.env"
    env_file.write_text(ENV + "BLT_PREFECT_BIND_ADDRESS=127.0.0.1\n")
    result = subprocess.run(
        ["bash", "deploy/collector.sh", *args],
        cwd=ROOT,
        env={
            **clean_environment(),
            "BLT_ENV_FILE": str(env_file),
            "BLT_COLLECTOR_SSH": str(fake(tmp_path, "ssh", remote_image)),
            "BLT_COLLECTOR_PODMAN": str(fake(tmp_path, "podman", "sha-new")),
        },
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def calls(tmp_path: Path, name: str) -> list[tuple[list[str], str]]:
    log = (tmp_path / f"{name}.log").read_text(errors="replace")  # the image is gzip
    found = []
    for chunk in log.split("--end--\n"):
        if chunk:
            args, _, stdin = chunk.partition("--stdin--\n")
            found.append((args.splitlines(), stdin))
    return found


def test_a_collector_gets_its_settings_on_its_input_never_on_a_command_line(
    tmp_path: Path,
) -> None:
    collector(tmp_path, "push", "tools-box", "tools01")
    ssh = calls(tmp_path, "ssh")
    commands = [args[-1] for args, _ in ssh]
    assert all(args[0] == "tools-box" for args, _ in ssh)
    # The image went over (it differed), then the settings, then the run.
    assert commands[1] == "gunzip | podman load"
    assert "collector-tools01.env" in commands[2] and commands[2].startswith("umask 077")
    assert "--name blt-collector-tools01 --network host" in commands[3]
    assert "--env-file ~/.config/blt/collector-tools01.env" in commands[3]
    settings = dict(line.split("=", 1) for line in ssh[2][1].splitlines())
    assert settings == {
        "BLT_COLLECTOR": "tools01",
        "PREFECT_API_URL": "http://127.0.0.1:14200/api",
        "PREFECT_API_AUTH_STRING": "admin:ui-secret",
        "BLT_API_URL": "http://127.0.0.1:18088",
        "BLT_API_KEY": "key-1",
        "BLT_MAX_PARALLEL": "4",
    }
    every_argument = "\n".join(
        a for name in ("ssh", "podman") for c, _ in calls(tmp_path, name) for a in c
    )
    assert "ui-secret" not in every_argument and "key-1" not in every_argument


def test_an_image_the_collector_has_is_not_sent_again(tmp_path: Path) -> None:
    collector(tmp_path, "push", "tools-box", "tools01", remote_image="sha-new")
    assert not any("podman load" in args[-1] for args, _ in calls(tmp_path, "ssh"))


def test_the_tunnel_brings_prefect_and_the_api_to_the_collector_s_loopback(
    tmp_path: Path,
) -> None:
    collector(tmp_path, "tunnel", "tools-box")
    ((args, _),) = calls(tmp_path, "ssh")
    assert args[0] == "-N" and args[-1] == "tools-box"
    assert "ExitOnForwardFailure=yes" in args
    forwards = [args[i + 1] for i, a in enumerate(args) if a == "-R"]
    assert forwards == ["127.0.0.1:14200:127.0.0.1:4200", "127.0.0.1:18088:127.0.0.1:8188"]

    config = collector(tmp_path, "ssh-config")
    assert "RemoteForward 127.0.0.1:14200 127.0.0.1:4200" in config
    assert "RemoteForward 127.0.0.1:18088 127.0.0.1:8188" in config


@pytest.mark.parametrize("name", ["default", "Tools01", "-x", "a b"])
def test_a_bad_collector_name_is_refused(tmp_path: Path, name: str) -> None:
    env_file = tmp_path / "blt.env"
    env_file.write_text(ENV)
    result = subprocess.run(
        ["bash", "deploy/collector.sh", "push", "tools-box", name],
        cwd=ROOT,
        env={**clean_environment(), "BLT_ENV_FILE": str(env_file), "BLT_COLLECTOR_SSH": "false"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and "collector's name" in result.stderr


def test_blt_s_own_worker_leaves_remote_collectors_runs_alone() -> None:
    start = (ROOT / "deploy/prefect/worker/start.sh").read_text()
    own, remote = (
        start.rsplit("exec prefect worker start", 1)[1],
        start.split("exec prefect worker start", 1)[1],
    )
    assert "--work-queue default" in own
    assert '--work-queue "$COLLECTOR"' in remote
    assert "BLT_COLLECTOR" in start


def run_ssh_config(tmp_path: Path, env: str) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / "blt.env"
    env_file.write_text(env)
    return subprocess.run(
        ["bash", "deploy/collector.sh", "ssh-config"],
        cwd=ROOT,
        env={**clean_environment(), "BLT_ENV_FILE": str(env_file)},
        capture_output=True,
        text=True,
    )


def test_a_command_in_blt_env_stops_the_scripts_before_it_runs(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    pasted = f"for db in blt prefect; do\n  touch {marker} # secret-ish\ndone\n"
    result = run_ssh_config(tmp_path, ENV + "\n# a comment\n" + pasted)
    lines = len(ENV.splitlines()) + 2
    assert result.returncode == 1
    assert f"line {lines + 1},{lines + 2},{lines + 3} is not a setting" in result.stderr
    assert "secret-ish" not in result.stderr and "touch" not in result.stderr
    assert not marker.exists()


def test_settings_comments_export_and_windows_line_ends_are_fine(tmp_path: Path) -> None:
    env = "# blt\r\n\r\n   # indented comment\r\nexport BLT_MAX_PARALLEL=2\r\n" + ENV.replace(
        "\n", "\r\n"
    )
    result = run_ssh_config(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert "RemoteForward" in result.stdout


def test_the_prefect_server_keeps_its_slot_leases_on_the_volume() -> None:
    server = (ROOT / "deploy/prefect/server/Containerfile").read_text()
    assert "PREFECT_HOME=/var/lib/prefect" in server
    assert "lease_storage.filesystem" in server
    up = (ROOT / "deploy/up.sh").read_text()
    assert "-v blt-prefect-home:/var/lib/prefect" in up
    worker = (ROOT / "deploy/prefect/worker/Containerfile").read_text()
    assert "PREFECT_FLOWS_HEARTBEAT_FREQUENCY=30" in worker
    start = (ROOT / "deploy/prefect/worker/start.sh").read_text()
    own = start.rsplit("exec prefect worker start", 1)[0].split("\nfi\n", 2)[-1]
    assert "python -m blt.flows.automations" in own  # blt's own worker, not a collector


def test_maintenance_runs_inside_the_api_container(tmp_path: Path) -> None:
    env_file = tmp_path / "blt.env"
    env_file.write_text(ENV)
    log = tmp_path / "podman.log"
    podman = tmp_path / "podman"
    podman.write_text(f'#!/bin/sh\necho "$*" >> {log}\ncase "$1" in container) echo true ;; esac\n')
    podman.chmod(0o755)
    base = {
        **clean_environment(),
        "BLT_ENV_FILE": str(env_file),
        "BLT_MAINTENANCE_PODMAN": str(podman),
    }

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "deploy/maintenance.sh", *args],
            cwd=ROOT,
            env=base,
            capture_output=True,
            text=True,
        )

    assert run("unstick", "--dry-run").returncode == 0
    calls = log.read_text().splitlines()
    assert calls[-1] == "exec blt-api python -m blt.api.maintenance unstick --dry-run"
    assert "key-1" not in log.read_text()  # the key stays in the container
    assert run("restart-everything").returncode == 2


def test_the_tunnel_is_kept_up_by_a_systemd_user_service(tmp_path: Path) -> None:
    units = tmp_path / "units"
    env_file = tmp_path / "blt.env"
    env_file.write_text(ENV + "BLT_PREFECT_BIND_ADDRESS=127.0.0.1\n")
    environment = {
        **clean_environment(),
        "BLT_ENV_FILE": str(env_file),
        "BLT_COLLECTOR_SYSTEMCTL": str(fake(tmp_path, "systemctl", "-")),
        "BLT_COLLECTOR_UNIT_DIR": str(units),
    }

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "deploy/collector.sh", *args],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )

    kept = run("keep-tunnel", "blue@tools-box")
    assert kept.returncode == 0, kept.stderr
    unit = units / "blt-tunnel-blue_tools-box.service"
    text = unit.read_text()
    assert f"ExecStart={ROOT}/deploy/collector.sh tunnel blue@tools-box" in text
    assert "Restart=always" in text and "Environment=BLT_TUNNEL_BATCH=1" in text
    assert [args for args, _ in calls(tmp_path, "systemctl")] == [
        ["--user", "daemon-reload"],
        ["--user", "enable", "--now", "blt-tunnel-blue_tools-box.service"],
    ]

    dropped = run("drop-tunnel", "blue@tools-box")
    assert dropped.returncode == 0, dropped.stderr
    assert not unit.exists()
    assert run("keep-tunnel", "tools box; rm -rf /").returncode == 1


def test_the_tunnel_from_the_service_never_waits_at_a_prompt(tmp_path: Path) -> None:
    env_file = tmp_path / "blt.env"
    env_file.write_text(ENV + "BLT_PREFECT_BIND_ADDRESS=127.0.0.1\n")
    result = subprocess.run(
        ["bash", "deploy/collector.sh", "tunnel", "tools-box"],
        cwd=ROOT,
        env={
            **clean_environment(),
            "BLT_ENV_FILE": str(env_file),
            "BLT_COLLECTOR_SSH": str(fake(tmp_path, "ssh", "-")),
            "BLT_TUNNEL_BATCH": "1",
        },
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stderr
    ((args, _),) = calls(tmp_path, "ssh")
    assert "BatchMode=yes" in args and args[-1] == "tools-box"


# --- moving blt to another machine (deploy/move.sh) ---

MOVE_FAKE = """#!/usr/bin/env bash
# Records each call like FAKE, and answers what move.sh asks.
{{ printf '%s\\n' "$@"; echo "--stdin--"; [[ -t 0 ]] || cat; echo "--end--"; }} >> {log}
case "$*" in
    *"volume exists"*) exit {volume} ;;
    *"test -f"*) exit 1 ;;
    *"unprivileged_port_start"*) echo {lowest} ;;
    *hostname*) echo tools.example.test ;;
    *"container inspect"*) echo true ;;
    *"image inspect"*) echo {image_id} ;;
    *pg_dump*) echo dump-bytes ;;
esac
"""


def move(
    tmp_path: Path, *args: str, volume: int = 1, lowest: int = 443
) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / "blt.env"
    env_file.write_text(ENV + "BLT_PREFECT_BIND_ADDRESS=127.0.0.1\n")
    tools = {}
    for name, image_id in (("ssh", "sha-old"), ("podman", "sha-new")):
        path = tmp_path / name
        path.write_text(
            MOVE_FAKE.format(
                log=tmp_path / f"{name}.log", volume=volume, lowest=lowest, image_id=image_id
            )
        )
        path.chmod(0o755)
        tools[name] = str(path)
    return subprocess.run(
        ["bash", "deploy/move.sh", "tools-box", *args],
        cwd=ROOT,
        env={
            **clean_environment(),
            "BLT_ENV_FILE": str(env_file),
            "BLT_MOVE_SSH": tools["ssh"],
            "BLT_MOVE_PODMAN": tools["podman"],
            "BLT_SKIP_BUILD": "true",
        },
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


def needs_clean_checkout() -> None:
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
    ).stdout
    if dirty.strip():
        pytest.skip("move.sh refuses a checkout with uncommitted changes")


def test_a_move_sends_settings_on_input_with_the_ui_on_its_new_port(tmp_path: Path) -> None:
    needs_clean_checkout()
    result = move(tmp_path, "--adopt-collector", "us")
    assert result.returncode == 0, result.stderr
    ssh = calls(tmp_path, "ssh")
    sent = next(stdin for args, stdin in ssh if "cat > 'blt/config/blt.env'" in args[-1])
    assert "BLT_API_KEY=key-1" in sent
    assert sent.rstrip().splitlines()[-3:] == [
        "BLT_UI_BIND_ADDRESS=0.0.0.0",
        "BLT_UI_PORT=443",
        "BLT_UI_ORIGIN=http://tools.example.test:443",
    ]
    commands = [args[-1] for args, _ in ssh]
    # Started empty, stopped, both databases restored, started again.
    starts = [i for i, c in enumerate(commands) if "BLT_SKIP_BUILD=true ./deploy/up.sh" in c]
    restores = [i for i, c in enumerate(commands) if "pg_restore --clean" in c]
    assert len(starts) == 2 and len(restores) == 2 and starts[0] < restores[0] < starts[1]
    assert any("-d blt" in commands[i] for i in restores)
    assert any("-d prefect" in commands[i] for i in restores)
    # The adopted collector goes to the Prefect and API beside it.
    adopt = next(c for c in commands if "blt-collector-us" in c and "sed -i" in c)
    assert "PREFECT_API_URL=http://127.0.0.1:4200/api" in adopt
    assert "BLT_API_URL=http://127.0.0.1:8188" in adopt
    every_argument = "\n".join(
        a for name in ("ssh", "podman") for c, _ in calls(tmp_path, name) for a in c
    )
    for secret in ("key-1", "pg-secret", "prefect-secret", "ui-secret", "session-secret"):
        assert secret not in every_argument


def test_a_move_never_lands_on_blt_s_data(tmp_path: Path) -> None:
    result = move(tmp_path, volume=0)
    assert result.returncode == 1 and "already has blt's data" in result.stderr
    assert not any("tar -x" in args[-1] for args, _ in calls(tmp_path, "ssh"))


def test_a_ui_port_below_1024_needs_the_host_to_allow_it(tmp_path: Path) -> None:
    result = move(tmp_path, lowest=1024)
    assert result.returncode == 1 and "ip_unprivileged_port_start=443" in result.stderr
    assert not any("tar -x" in args[-1] for args, _ in calls(tmp_path, "ssh"))


def test_an_upgrade_backs_up_first_and_keeps_the_data_and_settings_there(tmp_path: Path) -> None:
    needs_clean_checkout()
    result = move(tmp_path, "--upgrade", volume=0)
    assert result.returncode == 0, result.stderr
    commands = [args[-1] for args, _ in calls(tmp_path, "ssh")]
    backup = next(i for i, c in enumerate(commands) if "./deploy/backup.sh" in c)
    unpack = next(i for i, c in enumerate(commands) if "tar -x" in c)
    assert backup < unpack
    assert "blt.previous" in commands[unpack] and "config/." in commands[unpack]
    assert not any("pg_restore" in c or "cat > 'blt/config/blt.env'" in c for c in commands)
    assert any("BLT_SKIP_BUILD=true ./deploy/up.sh" in c for c in commands[unpack:])


def test_an_upgrade_needs_a_blt_there(tmp_path: Path) -> None:
    result = move(tmp_path, "--upgrade", volume=1)
    assert result.returncode == 1 and "no blt in blt to upgrade" in result.stderr
