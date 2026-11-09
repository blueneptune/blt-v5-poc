"""Stuck runs from a terminal: what Administration -> Schedules shows and
its "Clear stuck runs", for when the web UI is not there.

Run inside the API container, which has the API key and knows where the
API listens - so the key is never on a command line:

    deploy/maintenance.sh status
    deploy/maintenance.sh unstick [--dry-run] [--stuck-after-minutes N]

    (on the cluster) kubectl -n blt exec deploy/blt-api -- python -m blt.api.maintenance status

`status` exits 0 when runs are getting through and 1 when they are not,
so it can be checked from cron or a monitor.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import httpx


def api_client() -> httpx.Client:
    host = os.environ.get("BLT_LISTEN_HOST", "127.0.0.1")
    if host in ("0.0.0.0", "::", ""):  # noqa: S104 - listening everywhere: ask locally
        host = "127.0.0.1"
    port = os.environ.get("BLT_LISTEN_PORT", "8000")
    key = os.environ.get("BLT_API_KEY")
    if not key:
        sys.exit("BLT_API_KEY is not set: run this inside the blt-api container.")
    return httpx.Client(
        base_url=f"http://{host}:{port}/v1/admin/", headers={"X-API-Key": key}, timeout=60
    )


def _get(http: httpx.Client, path: str, **params: Any) -> Any:
    response = http.get(path, params=params)
    if response.status_code >= 400:
        sys.exit(f"The API said {response.status_code}: {response.text}")
    return response.json()


def describe(health: dict[str, Any], shown: int = 20) -> list[str]:
    lines = []
    slots = health.get("slots")
    if slots:
        lines.append(
            f"Collection slots: {slots['active']} of {slots['limit']} taken; "
            f"{health['collecting']} runs collecting"
            + (
                f"; {health['leaked_slots']} held by runs that are gone"
                if health["leaked_slots"]
                else ""
            )
        )
    else:
        lines.append("Collection slots: none yet (blt's worker has not started).")
    stuck = health["stuck"]
    if stuck:
        lines.append(
            f"Stuck runs: {len(stuck)} (running over {health['stuck_after_minutes']} min, or "
            "starting or cancelling over 10 min)"
        )
        for run in stuck[:shown]:
            lines.append(
                f"  {run['name']:<32} {run.get('flow_name') or '':<32} "
                f"{run.get('state_name') or run.get('state_type')!s:<10} {run['stuck_minutes']} min"
            )
        if len(stuck) > shown:
            lines.append(f"  and {len(stuck) - shown} more")
    lines.append("Runs are getting through." if health["healthy"] else "Runs are stuck.")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="maintenance", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "unstick"):
        command = sub.add_parser(name)
        command.add_argument("--stuck-after-minutes", type=int, default=120)
        if name == "unstick":
            command.add_argument(
                "--dry-run", action="store_true", help="Only say what it would do."
            )
    args = parser.parse_args(argv)

    with api_client() as http:
        health = _get(http, "runs/health", stuck_after_minutes=args.stuck_after_minutes)
        print("\n".join(describe(health)))
        if args.command == "status":
            return 0 if health["healthy"] else 1
        if health["healthy"]:
            print("Nothing to clear.")
            return 0
        if args.dry_run:
            print(
                f"Would cancel {len(health['stuck'])} runs and bring the slot count down to the "
                "runs still collecting. Run it without --dry-run to do so."
            )
            return 0
        response = http.post(
            "runs/clear-stuck",
            json={
                "run_ids": [run["id"] for run in health["stuck"]],
                "stuck_after_minutes": args.stuck_after_minutes,
            },
        )
        if response.status_code >= 400:
            sys.exit(f"The API said {response.status_code}: {response.text}")
        cleared = response.json()
        print(
            f"Cancelled {len(cleared['cancelled'])} runs ({cleared['skipped']} had moved on); "
            f"slots taken {cleared['slots_before']} -> {cleared['slots_after']}."
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
