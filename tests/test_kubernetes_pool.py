"""blt's Kubernetes work pool (docs/kubernetes.md): each flow run its own
pod, from the worker's image, with only what a run needs."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
prefect_kubernetes = pytest.importorskip("prefect_kubernetes.worker")


def template() -> dict:
    from blt.flows.kubernetes import job_template

    return job_template(
        prefect_kubernetes.KubernetesWorker.get_default_base_job_template(),
        image="registry.test/blt/prefect-worker:2026.11.13",
        namespace="blt",
        api_url="http://blt-api.blt.svc:8000",
        secret="blt-app",
    )


def test_run_pods_use_the_workers_image_in_its_namespace() -> None:
    variables = template()["variables"]["properties"]
    assert variables["image"]["default"] == "registry.test/blt/prefect-worker:2026.11.13"
    assert variables["namespace"]["default"] == "blt"
    assert variables["image_pull_policy"]["default"] == "IfNotPresent"
    # Prefect retries a run itself; Kubernetes must not run it again too.
    assert variables["backoff_limit"]["default"] == 0
    assert variables["finished_job_ttl"]["default"] == 3600


def test_a_run_pod_gets_the_api_key_by_reference_and_no_database_url() -> None:
    manifest = template()["job_configuration"]["job_manifest"]
    env = manifest["spec"]["template"]["spec"]["containers"][0]["env"]
    by_name = {entry["name"]: entry for entry in env}
    assert by_name["BLT_API_URL"]["value"] == "http://blt-api.blt.svc:8000"
    assert by_name["BLT_API_KEY"]["valueFrom"] == {
        "secretKeyRef": {"name": "blt-app", "key": "BLT_API_KEY"}
    }
    assert "value" not in by_name["BLT_API_KEY"]
    assert not any("DATABASE" in name for name in by_name)


def test_prefect_renders_a_run_job_from_it() -> None:
    import asyncio
    import uuid

    from prefect.client.schemas.objects import FlowRun

    configuration = asyncio.run(
        prefect_kubernetes.KubernetesWorkerJobConfiguration.from_template_and_values(template(), {})
    )
    configuration.prepare_for_flow_run(FlowRun(id=uuid.uuid4(), name="x", flow_id=uuid.uuid4()))
    job = configuration.job_manifest
    container = job["spec"]["template"]["spec"]["containers"][0]
    names = [entry["name"] for entry in container["env"]]
    # Prefect's own first, then blt's.
    assert "PREFECT__FLOW_RUN_ID" in names
    assert names[-3:] == ["BLT_API_URL", "BLT_API_KEY", "DO_NOT_TRACK"]
    assert container["image"] == "registry.test/blt/prefect-worker:2026.11.13"
    assert job["metadata"]["namespace"] == "blt"
    assert job["spec"]["backoffLimit"] == 0


def fake_bin(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "calls.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "prefect").write_text(
        f'#!/bin/sh\necho "prefect $* pool=$BLT_WORK_POOL" >> {log}\n'
        'case "$*" in *"work-pool inspect"*) exit 1 ;; esac\n'
    )
    (bin_dir / "python").write_text(
        f'#!/bin/sh\necho "python $*" >> {log}\n'
        'if [ "$1 $2" = "-m blt.flows.kubernetes" ]; then echo "{{}}"; fi\n'
        "cat > /dev/null\n"
    )
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    return bin_dir, log


def start(tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], str]:
    bin_dir, log = fake_bin(tmp_path)
    result = subprocess.run(
        ["sh", str(ROOT / "deploy/prefect/worker/start.sh")],
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "PREFECT_API_URL": "http://prefect.test/api",
            **env,
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    return result, log.read_text() if log.exists() else ""


def test_a_kubernetes_worker_writes_the_pool_template_and_runs_pods(tmp_path: Path) -> None:
    result, calls = start(tmp_path, BLT_WORKER_TYPE="kubernetes", BLT_WORK_POOL="blt-k8s")
    assert result.returncode == 0, result.stderr
    assert "python -m blt.flows.kubernetes" in calls
    assert "work-pool create blt-k8s --type kubernetes --base-job-template" in calls
    assert "--overwrite" in calls
    assert "deploy --all --prefect-file /app/prefect.yaml pool=blt-k8s" in calls
    last = calls.strip().splitlines()[-1]
    assert last.startswith("prefect worker start --pool blt-k8s --type kubernetes")
    assert "--work-queue default" in last


def test_podman_stays_a_process_worker(tmp_path: Path) -> None:
    result, calls = start(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "blt.flows.kubernetes" not in calls
    assert "work-pool create blt --type process" in calls
    assert "deploy --all --prefect-file /app/prefect.yaml pool=blt" in calls
    assert (
        calls.strip().splitlines()[-1].startswith("prefect worker start --pool blt --type process")
    )


def test_an_unknown_worker_type_is_refused(tmp_path: Path) -> None:
    result, calls = start(tmp_path, BLT_WORKER_TYPE="docker")
    assert result.returncode == 1
    assert "process or kubernetes" in result.stderr
    assert calls == ""


def test_every_deployment_is_on_the_configured_pool() -> None:
    import yaml

    deployments = yaml.safe_load((ROOT / "prefect.yaml").read_text())["deployments"]
    assert {d["work_pool"]["name"] for d in deployments} == {"{{ $BLT_WORK_POOL }}"}
