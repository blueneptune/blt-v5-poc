"""A remote collector's start, on Linux and on Windows alike.

A remote collector (docs/remote-collector.md) is a Prefect worker that
takes only its own work queue, named after it, of blt's work pool. This
waits for the Prefect server - for as long as it takes, since it is at
the far end of an ssh tunnel or a network that may not be up yet - makes
the queue if it is missing, then runs the worker:

    python -m blt.flows.collector                    # settings from the environment
    python -m blt.flows.collector --settings FILE    # or from a JSON file (Windows)
    python -m blt.flows.collector --settings FILE --check
                                                     # can it reach Prefect and the API? once

The worker image runs it when BLT_COLLECTOR is set
(deploy/prefect/worker/start.sh); the Windows service runs it with the
settings file install.ps1 wrote (docs/windows-collector.md).

Environment (or the settings file's keys): BLT_COLLECTOR, the
collector's name; BLT_WORK_POOL (blt); BLT_WORKER_TYPE (process); and
what the flow runs need - PREFECT_API_URL, PREFECT_API_AUTH_STRING,
BLT_API_URL, BLT_API_KEY, and SSL_CERT_FILE / PREFECT_API_SSL_CERT_FILE
for a server whose certificate is not publicly signed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import runpy
import socket
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from blt.queues import COLLECTOR_NAME, DEFAULT_QUEUE

log = logging.getLogger("blt.collector")

WORKER_TYPES = ("process", "kubernetes")
# How often a collector still waiting for Prefect says so.
SAY_EVERY_SECONDS = 300


def collector_name(value: str | None) -> str:
    """The collector's name, checked: it becomes the work queue's name
    and part of the worker's. Raises ValueError."""
    name = (value or "").strip()
    if not name or name == DEFAULT_QUEUE or not COLLECTOR_NAME.match(name):
        raise ValueError(
            "BLT_COLLECTOR must be lower case letters, digits and dashes, and not 'default'."
        )
    return name


def load_settings(path: Path, environ: dict[str, str] | None = None) -> list[str]:
    """Put a settings file's values in the environment, over what is
    there, and return their names (never their values: two are secrets).

    The file is a JSON object of names to strings, as install.ps1 writes
    it: JSON, so a password with any character in it needs no quoting
    rules. Read before Prefect is imported, since Prefect reads its
    settings from the environment when it is."""
    env = os.environ if environ is None else environ
    # utf-8-sig: Windows PowerShell 5.1 starts UTF-8 files with a BOM.
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ValueError(f"{path} is not a JSON object of names to strings.")
    env.update(data)
    return sorted(data)


def _prefect_health() -> Exception | None:
    from prefect.client.orchestration import get_client

    try:
        with get_client(sync_client=True) as client:
            return client.api_healthcheck()
    except Exception as error:  # whatever it is, Prefect is not answering yet
        return error


def _reason(error: Exception) -> str:
    """Why the last attempt failed, in one line, for the log. httpx's
    messages carry the URL and the cause (refused, certificate not
    trusted, ...), never the auth header."""
    text = str(error).strip().splitlines()
    return f"{type(error).__name__}: {text[0] if text else ''}".rstrip(": ")


def wait_for_prefect(
    limit_seconds: float = 0,
    *,
    healthcheck: Callable[[], Exception | None] = _prefect_health,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    say_every: float = SAY_EVERY_SECONDS,
) -> None:
    """Return once the Prefect server answers. limit_seconds 0 waits for
    as long as it takes; otherwise SystemExit after that long.

    It says why it is still waiting on the first failure and then every
    `say_every` seconds: a certificate not trusted or a refused
    connection should be in the log, not hidden by retries."""
    url = os.environ.get("PREFECT_API_URL", "(PREFECT_API_URL is not set)")
    log.info("Waiting for the Prefect server at %s", url)
    started = clock()
    said: float | None = None
    while True:
        error = healthcheck()
        if error is None:
            log.info("The Prefect server answers.")
            return
        now = clock()
        if limit_seconds and now - started > limit_seconds:
            raise SystemExit(
                f"The Prefect server did not answer within {limit_seconds:g} s ({_reason(error)})."
            )
        if said is None or now - said >= say_every:
            log.info(
                "Still waiting for the Prefect server (%s) - is the tunnel up, or blt's server "
                "reachable on 443?",
                _reason(error),
            )
            said = now
        sleep(3)


def ensure_queue(name: str, pool: str, client: Any = None) -> bool:
    """Make the collector's work queue if it is missing; True if it made
    it. A worker does not make its queue, and asking a missing one for
    work is refused. The pool is blt's own worker's to make."""
    from prefect.exceptions import ObjectAlreadyExists, ObjectNotFound

    if client is None:
        from prefect.client.orchestration import get_client

        with get_client(sync_client=True) as made:
            return ensure_queue(name, pool, made)
    try:
        client.read_work_queue_by_name(name, work_pool_name=pool)
        return False
    except ObjectNotFound:
        pass
    try:
        client.create_work_queue(name=name, work_pool_name=pool)
    except ObjectAlreadyExists:
        # Another machine running the same collector made it first.
        return False
    return True


def check(pool: str) -> list[str]:
    """Can this machine reach blt, with these settings? What is wrong,
    if anything: for install.ps1 to show before it starts the service.

    Prefect: an answer to a call that needs its password. blt's API: its
    /healthz, which needs no key - the collector's key is only checked
    when it first sends what it collected. Both over the same TLS
    settings as the collector's own calls."""
    import httpx
    from prefect.client.orchestration import get_client
    from prefect.exceptions import ObjectNotFound

    problems = []
    prefect_url = os.environ.get("PREFECT_API_URL", "")
    try:
        with get_client(sync_client=True) as client:
            error = client.api_healthcheck()
            if error is not None:
                raise error
            try:
                client.read_work_pool(pool)
            except ObjectNotFound:
                problems.append(
                    f"Prefect at {prefect_url} answers, but has no work pool {pool} yet: "
                    "start blt's own worker first (BLT_PREFECT=true)."
                )
    except Exception as error:
        problems.append(f"Prefect at {prefect_url}: {_reason(error)}")
    api_url = os.environ.get("BLT_API_URL", "").rstrip("/")
    try:
        # httpx's own verification: certifi's CAs, or SSL_CERT_FILE's.
        httpx.get(f"{api_url}/healthz", timeout=15).raise_for_status()
    except Exception as error:
        problems.append(f"blt's API at {api_url}: {_reason(error)}")
    return problems


def worker_command(name: str, pool: str, worker_type: str, host: str) -> list[str]:
    """The Prefect worker for this collector's queue, run by this Python
    (the bundled one on Windows, where there is no `prefect` on PATH)."""
    return [
        sys.executable,
        "-m",
        "prefect",
        "worker",
        "start",
        "--pool",
        pool,
        "--type",
        worker_type,
        "--work-queue",
        name,
        "--name",
        f"blt-collector-{name}-{host}",
    ]


def run_worker(command: list[str]) -> int:
    """Run the worker. On Linux this process becomes it, as `exec` did in
    start.sh. Windows has no exec - os.execv there starts a new process
    and ends this one, which the service wrapper takes for the service
    ending - and a child process would outlive this one if it were
    killed, leaving two workers after the restart. So there the worker
    runs in this process, as `python -m prefect` would run it."""
    if sys.platform != "win32":
        os.execv(command[0], command)
    sys.argv = ["prefect", *command[3:]]
    runpy.run_module("prefect", run_name="__main__", alter_sys=True)
    return 0


def settings_from(environ: Mapping[str, str]) -> tuple[str, str, str]:
    """Name, pool and worker type from the environment. Raises ValueError."""
    name = collector_name(environ.get("BLT_COLLECTOR"))
    pool = environ.get("BLT_WORK_POOL") or "blt"
    worker_type = environ.get("BLT_WORKER_TYPE") or "process"
    if worker_type not in WORKER_TYPES:
        raise ValueError(f"BLT_WORKER_TYPE must be process or kubernetes, not '{worker_type}'.")
    return name, pool, worker_type


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m blt.flows.collector", description="Run a blt remote collector."
    )
    parser.add_argument("--settings", type=Path, help="a JSON file of settings (Windows)")
    parser.add_argument(
        "--check", action="store_true", help="only see whether Prefect and the API answer"
    )
    parser.add_argument(
        "--wait-only",
        type=float,
        metavar="SECONDS",
        help="only wait for Prefect, at most this long (0: for ever), then exit",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    # Not a line per health check: the waiting says what matters.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.settings:
        names = load_settings(args.settings)
        log.info("Settings from %s: %s", args.settings, ", ".join(names))
    if args.wait_only is not None:
        wait_for_prefect(args.wait_only)
        return 0
    if args.check:
        problems = check(os.environ.get("BLT_WORK_POOL") or "blt")
        for problem in problems:
            log.error("%s", problem)
        if not problems:
            log.info("Prefect and blt's API both answer.")
        return 1 if problems else 0

    try:
        name, pool, worker_type = settings_from(os.environ)
    except ValueError as error:
        log.error("%s", error)
        return 1
    # prefect.yaml and blt.queues read the pool's name from here too.
    os.environ["BLT_WORK_POOL"] = pool
    wait_for_prefect(0)
    log.info("Remote collector %s: taking work queue %s of pool %s", name, name, pool)
    if ensure_queue(name, pool):
        log.info("Made work queue %s.", name)
    host = socket.gethostname().split(".")[0].lower() or "unknown"
    return run_worker(worker_command(name, pool, worker_type, host))


if __name__ == "__main__":
    sys.exit(main())
