"""The automation that marks silent flow runs Crashed (blt.flows.automations)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from prefect.client.schemas.objects import StateType

from blt.flows import automations


def test_a_run_silent_after_a_heartbeat_is_crashed() -> None:
    made = automations.automation()
    trigger: Any = made.trigger
    assert made.name == "blt-crash-silent-runs" and made.enabled
    assert trigger.posture == "Proactive"
    assert trigger.after == {"prefect.flow-run.heartbeat"}
    assert trigger.for_each == {"prefect.resource.id"}
    assert trigger.within == timedelta(seconds=automations.SILENT_SECONDS)
    # Alive, done, or waiting to retry: none of these is a dead run.
    for event in ("heartbeat", "Completed", "Failed", "Cancelled", "AwaitingRetry", "Paused"):
        assert f"prefect.flow-run.{event}" in trigger.expect
    (action,) = made.actions
    assert action.type == "change-flow-run-state"
    assert action.state == StateType.CRASHED


def test_ensure_replaces_the_automation_of_that_name() -> None:
    calls: list[tuple[str, Any]] = []
    old = [SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4())]

    class Client:
        def read_automations_by_name(self, name: str) -> list[Any]:
            calls.append(("read", name))
            return old

        def delete_automation(self, automation_id: uuid.UUID) -> None:
            calls.append(("delete", automation_id))

        def create_automation(self, automation: Any) -> uuid.UUID:
            calls.append(("create", automation.name))
            return uuid.uuid4()

    automations.ensure(Client())  # type: ignore[arg-type]
    assert calls == [
        ("read", "blt-crash-silent-runs"),
        ("delete", old[0].id),
        ("delete", old[1].id),
        ("create", "blt-crash-silent-runs"),
    ]
