# blt on Kubernetes: a pod for each run

Under podman (WSL, a tools server) the Prefect worker runs each flow run as
a process of its own container. On a cluster it can instead start a pod
for each run: a check, one source's collection, a parent fanning out. The
worker itself runs no flow. It creates a Kubernetes Job per run and
watches it.

A run's pod:

- uses **the worker's own image**, the one the worker pod runs, so a
  release changes both together;
- goes in **the worker's namespace**;
- has the Prefect API's address and auth string, and blt's API address and
  key, the auth string and key as references to a Secret, **never in the
  Job itself**;
- has **no database URL**: runs write through the API, as everywhere;
- is **not retried by Kubernetes** (`backoffLimit: 0`). Prefect retries a
  failed run itself (each flow's `retries`), and both would run it twice;
- stays **an hour after it ends**, for `kubectl logs`, and is then removed.
  The run's log stays in Prefect.

Requests: 100m CPU and 256Mi of memory, limited to 1Gi. Each is a
variable of the work pool, so the Prefect UI can change them for one
deployment (*Edit → Job variables*), e.g. `{"memory_limit": "2Gi"}`.

## Turning it on

Set these on the worker Deployment:

| Variable | Value |
|---|---|
| `BLT_WORKER_TYPE` | `kubernetes` (`process` is the default) |
| `BLT_WORK_POOL` | a pool name not used before, e.g. `blt-k8s` |
| `PREFECT_INTEGRATIONS_KUBERNETES_WORKER_API_AUTH_STRING_SECRET_NAME` | the Secret holding `PREFECT_AUTH` and `BLT_API_KEY` (`blt-app`) |
| `PREFECT_INTEGRATIONS_KUBERNETES_WORKER_API_AUTH_STRING_SECRET_KEY` | `PREFECT_AUTH` |

Then set **`BLT_WORK_POOL` on the API Deployment too**: its Administration
pages and Run now go to that pool's queues.

The worker needs a service account that may manage Jobs and read pods
in its namespace (create, watch and delete Jobs; read pods, their logs and
events). yoki-blt-infra has it.

Each start writes the pool's job template (`python -m blt.flows.kubernetes`)
and points every deployment at the pool, so the pool stays in step with
the image.

**Why a new pool name:** a work pool's type cannot change. The old
process pool `blt` stays, unused. A run scheduled on it before the switch
waits there with nothing to take it (*Late* in the Prefect UI); delete
those runs.

Optional: `BLT_RUN_IMAGE` and `BLT_RUN_NAMESPACE` override the image and
namespace the worker finds for itself.

## Remote collectors

A remote collector is a process worker taking its own queue of the same
pool. Its queue on a Kubernetes pool is not supported yet: Prefect warns
of a worker type mismatch. A collector in another cluster would run as a
Kubernetes worker there, which needs its own namespace in the template.
That is the next step when a location cluster gets one.
