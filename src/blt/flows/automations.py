"""The Prefect automation that marks a flow run Crashed when it goes
silent: its process died - the worker restarted, the machine went down -
and nothing else will ever end it.

Each flow run's process sends a heartbeat event every
PREFECT_FLOWS_HEARTBEAT_FREQUENCY seconds (30, set in the worker image).
When one has sent a heartbeat and then nothing - no heartbeat, no final
state - for SILENT_SECONDS, the automation sets it to Crashed. The next
scheduled run then catches up, and the run's collection slot comes back
when its lease runs out (the Prefect server keeps leases on disk:
deploy/prefect/server/Containerfile).

SILENT_SECONDS is generous so a remote collector's run survives a short
drop of its ssh tunnel (docs/remote-collector.md).

blt's own worker sets it up on every start (deploy/prefect/worker/start.sh):
`python -m blt.flows.automations`. It replaces the automation of this name,
so a new image brings its version along.
"""

from __future__ import annotations

from datetime import timedelta

from prefect.client.orchestration import SyncPrefectClient, get_client
from prefect.client.schemas.objects import StateType
from prefect.events.actions import ChangeFlowRunState
from prefect.events.schemas.automations import AutomationCore, EventTrigger, Posture

NAME = "blt-crash-silent-runs"
SILENT_SECONDS = 300

# Events that say a run is alive, or done: any of these within
# SILENT_SECONDS of a heartbeat and it is not silent. A run waiting to
# retry, or paused, sends no heartbeats and is not dead either.
ALIVE_OR_DONE = [
    "prefect.flow-run.heartbeat",
    "prefect.flow-run.Completed",
    "prefect.flow-run.Failed",
    "prefect.flow-run.Cancelled",
    "prefect.flow-run.Crashed",
    "prefect.flow-run.AwaitingRetry",
    "prefect.flow-run.Retrying",
    "prefect.flow-run.Scheduled",
    "prefect.flow-run.Paused",
    "prefect.flow-run.Suspended",
]


def automation() -> AutomationCore:
    return AutomationCore(
        name=NAME,
        description=(
            "blt: a flow run that sent heartbeats and then went silent - its process is "
            f"gone - is marked Crashed after {SILENT_SECONDS} s (blt.flows.automations)."
        ),
        enabled=True,
        trigger=EventTrigger(
            match={"prefect.resource.id": "prefect.flow-run.*"},
            after={"prefect.flow-run.heartbeat"},
            expect=set(ALIVE_OR_DONE),
            for_each={"prefect.resource.id"},
            posture=Posture.Proactive,
            threshold=1,
            within=timedelta(seconds=SILENT_SECONDS),
        ),
        actions=[
            ChangeFlowRunState(
                state=StateType.CRASHED,
                name="Crashed",
                message=(
                    f"No heartbeat for {SILENT_SECONDS} s: its process is gone (a worker "
                    "restart?). The next scheduled run catches up."
                ),
            )
        ],
    )


def ensure(client: SyncPrefectClient) -> None:
    """Replace the automation by this name with this image's version."""
    for existing in client.read_automations_by_name(NAME):
        client.delete_automation(existing.id)
    client.create_automation(automation())


def main() -> None:
    with get_client(sync_client=True) as client:
        ensure(client)
    print(f"Automation {NAME}: silent runs are marked Crashed after {SILENT_SECONDS} s")


if __name__ == "__main__":
    main()
