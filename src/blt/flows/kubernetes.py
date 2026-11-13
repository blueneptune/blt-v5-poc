"""blt's Kubernetes work pool: each flow run in a pod of its own.

On a cluster the worker (deploy/prefect/worker/start.sh with
BLT_WORKER_TYPE=kubernetes) runs no flow itself. For each run it creates
a Kubernetes Job, whose pod runs that one flow run from the worker's own
image and is gone when it finishes. See docs/kubernetes.md.

`python -m blt.flows.kubernetes` prints the pool's base job template:
Prefect's own for Kubernetes, with blt's defaults filled in. start.sh
writes it to the pool each time the worker starts, so the pool stays in
step with the image.

The pod gets what a flow run needs and no more:
- PREFECT_API_URL, from the worker, as Prefect always passes it.
- PREFECT_API_AUTH_STRING, as a reference to the Secret the worker names
  (PREFECT_INTEGRATIONS_KUBERNETES_WORKER_API_AUTH_STRING_SECRET_NAME and
  _KEY), so it never appears in the Job.
- BLT_API_URL, and BLT_API_KEY as a reference to the same Secret.

Not the database URL: flow runs write through the API.
"""

from __future__ import annotations

import copy
import json
import os
import ssl
import sys
from pathlib import Path
from typing import Any

import httpx

SERVICE_ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")
# What a run's pod asks for. A collection run is mostly waiting on the
# source's API; NetBackup and Rubrik pages are the largest it holds.
DEFAULTS: dict[str, Any] = {
    "image_pull_policy": "IfNotPresent",
    "cpu_request": "100m",
    "memory_request": "256Mi",
    "memory_limit": "1Gi",
    # A finished run's Job and pod stay an hour for `kubectl logs`; the
    # run's log is in Prefect for good.
    "finished_job_ttl": 3600,
    # Prefect retries a failed run itself (each flow's retries=); a pod
    # retried by Kubernetes as well would run it twice.
    "backoff_limit": 0,
}


def own_namespace() -> str:
    return (SERVICE_ACCOUNT / "namespace").read_text().strip()


def own_image(namespace: str, pod: str) -> str:
    """The image of the pod this runs in - the worker's - read from the
    Kubernetes API with the pod's own service account."""
    token = (SERVICE_ACCOUNT / "token").read_text().strip()
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    tls = ssl.create_default_context(cafile=str(SERVICE_ACCOUNT / "ca.crt"))
    response = httpx.get(
        f"https://{host}:{port}/api/v1/namespaces/{namespace}/pods/{pod}",
        headers={"Authorization": f"Bearer {token}"},
        verify=tls,
        timeout=30,
    )
    response.raise_for_status()
    containers = response.json()["spec"]["containers"]
    return str(containers[0]["image"])


def job_template(
    default: dict[str, Any],
    *,
    image: str,
    namespace: str,
    api_url: str,
    secret: str,
    api_key_key: str = "BLT_API_KEY",
) -> dict[str, Any]:
    """Prefect's default Kubernetes template with blt's values as the
    variables' defaults, and blt's own variables in the pod."""
    template = copy.deepcopy(default)
    properties = template["variables"]["properties"]
    for name, value in {**DEFAULTS, "image": image, "namespace": namespace}.items():
        properties[name]["default"] = value

    container = template["job_configuration"]["job_manifest"]["spec"]["template"]["spec"][
        "containers"
    ][0]
    # A list, not "{{ env }}": the worker puts Prefect's own variables in
    # front of these (prefect_kubernetes.worker._populate_env_in_manifest).
    container["env"] = [
        {"name": "BLT_API_URL", "value": api_url},
        {
            "name": "BLT_API_KEY",
            "valueFrom": {"secretKeyRef": {"name": secret, "key": api_key_key}},
        },
        {"name": "DO_NOT_TRACK", "value": "1"},
    ]
    return template


def main() -> None:
    from prefect_kubernetes.worker import KubernetesWorker  # type: ignore[import-untyped]

    namespace = os.environ.get("BLT_RUN_NAMESPACE") or own_namespace()
    image = os.environ.get("BLT_RUN_IMAGE") or own_image(namespace, os.environ["HOSTNAME"])
    secret = os.environ.get("PREFECT_INTEGRATIONS_KUBERNETES_WORKER_API_AUTH_STRING_SECRET_NAME")
    if not secret:
        sys.exit(
            "Set PREFECT_INTEGRATIONS_KUBERNETES_WORKER_API_AUTH_STRING_SECRET_NAME (and _KEY) "
            "to the Secret with the Prefect auth string and BLT_API_KEY - see docs/kubernetes.md."
        )
    template = job_template(
        KubernetesWorker.get_default_base_job_template(),
        image=image,
        namespace=namespace,
        api_url=os.environ["BLT_API_URL"],
        secret=secret,
    )
    json.dump(template, sys.stdout, indent=2)
    print()
    print(f"Run pods: {image} in namespace {namespace}", file=sys.stderr)


if __name__ == "__main__":
    main()
