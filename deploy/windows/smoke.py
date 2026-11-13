"""An end-to-end check of a collector, for CI's Windows job: a Prefect
server of its own, the collector's start (blt.flows.collector) against
it, and one flow run through the collector's queue - started by the
process worker as a process of the bundled Python, through prefect.yaml's
pull step (blt.flows.steps), to Completed.

    <bundle>\\python\\python.exe -I deploy/windows/smoke.py

It needs only the bundle's Python and a free port 4299 on 127.0.0.1.
Nothing here reaches blt: there is none.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PORT = 4299
API = f"http://127.0.0.1:{PORT}/api"
FLOW = """
from prefect import flow


@flow(name="blt-smoke")
def hello() -> str:
    return "collected"
"""


def wait_for(what: str, ready, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if ready():
                return
        except Exception:
            pass
        time.sleep(2)
    sys.exit(f"smoke: {what} did not happen within {seconds:g} s.")


def main() -> None:
    scratch = Path(tempfile.mkdtemp(prefix="blt-smoke-"))
    (scratch / "smoke_flow.py").write_text(FLOW)
    base = {k: v for k, v in os.environ.items() if not k.startswith(("PREFECT_", "BLT_"))}
    base |= {
        "PREFECT_HOME": str(scratch / "prefect"),
        "DO_NOT_TRACK": "1",
        "PYTHONUTF8": "1",
    }
    server_log = open(scratch / "server.log", "wb")
    server = subprocess.Popen(
        [sys.executable, "-m", "prefect", "server", "start", "--host", "127.0.0.1",
         "--port", str(PORT)],
        env=base | {"PREFECT_SERVER_UI_ENABLED": "false"},
        stdout=server_log, stderr=subprocess.STDOUT,
    )  # fmt: skip
    os.environ.update(base | {"PREFECT_API_URL": API})
    collector = None
    collector_log = open(scratch / "collector.log", "wb")
    try:
        wait_for(
            "the Prefect server answering",
            lambda: urllib.request.urlopen(f"{API}/health", timeout=5).status == 200,
            180,
        )
        from prefect.client.orchestration import get_client
        from prefect.client.schemas.actions import WorkPoolCreate

        with get_client(sync_client=True) as client:
            # blt's own worker makes the pool; here there is none.
            client.create_work_pool(WorkPoolCreate(name="blt", type="process"))
            collector = subprocess.Popen(
                [sys.executable, "-m", "blt.flows.collector"],
                env=dict(os.environ) | {"BLT_COLLECTOR": "smoke"},
                stdout=collector_log, stderr=subprocess.STDOUT,
            )  # fmt: skip
            wait_for(
                "the collector's worker asking for work",
                lambda: any(
                    w.name.startswith("blt-collector-smoke-")
                    for w in client.read_workers_for_work_pool("blt")
                ),
                180,
            )
            print("smoke: the collector made its queue and its worker is asking for work.")

            flow_id = client.create_flow_from_name("blt-smoke")
            deployment = client.create_deployment(
                flow_id=flow_id,
                name="smoke",
                work_pool_name="blt",
                work_queue_name="smoke",
                entrypoint="smoke_flow.py:hello",
                # prefect.yaml's step first, as every blt deployment has it
                # (there is no /app here), then the flow's own folder.
                pull_steps=[
                    {"blt.flows.steps.app_directory": {"directory": "/app"}},
                    {
                        "prefect.deployments.steps.set_working_directory": {
                            "directory": str(scratch)
                        }
                    },
                ],
            )
            run = client.create_flow_run_from_deployment(deployment)
            state = None

            def finished() -> bool:
                nonlocal state
                state = client.read_flow_run(run.id).state
                return bool(state and state.is_final())

            wait_for("the flow run finishing", finished, 300)
            assert state is not None
            print(f"smoke: flow run {run.name}: {state.type.value}")
            if not state.is_completed():
                sys.exit(f"smoke: the flow run ended {state.type.value}: {state.message}")
    finally:
        for process in (collector, server):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(30)
                except subprocess.TimeoutExpired:
                    process.kill()
        collector_log.close()
        server_log.close()
        for log in ("collector.log", "server.log"):
            text = (scratch / log).read_text(errors="replace")
            print(f"--- {log} (last lines)\n" + "\n".join(text.splitlines()[-40:]))


if __name__ == "__main__":
    main()
