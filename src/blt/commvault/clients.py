"""Clients, the agents installed on them, and their instances."""

from __future__ import annotations

from typing import Any

from ._base import Entities, Entity, Names, Resource


class Clients(Resource):
    def list(self) -> Entities:
        """Every client's entity (GET /Client): clientId, clientName,
        hostName, displayName, clientGUID."""
        body = self._list("/Client")
        return [c["client"]["clientEntity"] for c in body.get("clientProperties") or []]

    def find(self, name: str) -> Entity | None:
        wanted = name.lower()
        return next((c for c in self.list() if c.get("clientName", "").lower() == wanted), None)

    def agents(self, client_id: int) -> Names:
        """Names of the agents installed on a client (GET /Agent)."""
        body = self._list(f"/Agent?clientId={client_id}")
        return [
            a["idaEntity"]["appName"]
            for a in body.get("agentProperties") or []
            if not a.get("AgentProperties", {}).get("isMarkedDeleted")
        ]

    def delete(self, client_id: int, *, force: bool = True) -> None:
        """Remove a client and, with force, its backup history from the
        CommCell. Not reversible."""
        self._change("DELETE", f"/Client/{client_id}" + ("?forceDelete=1" if force else ""))


class Instances(Resource):
    def list(self, client_id: int) -> Entities:
        """Each instance's entity on a client (GET /Instance): instanceId,
        instanceName, appName, applicationId, clientId.

        instanceId is not unique across clients: every file system
        agent's "DefaultInstanceName" is instance 1."""
        body = self._list(f"/Instance?clientId={client_id}")
        return [i["instance"] for i in body.get("instanceProperties") or [] if "instance" in i]

    def get(self, instance_id: int) -> dict[str, Any]:
        """One instance's full properties (GET /Instance/{id})."""
        properties = self._list(f"/Instance/{instance_id}").get("instanceProperties") or [{}]
        first: dict[str, Any] = properties[0]
        return first

    def update(self, instance: dict[str, Any], properties: dict[str, Any]) -> None:
        """Change properties of an instance. `instance` is its entity as
        returned by list(); `properties` is merged into the request
        beside it (for example {"mssqlInstance": {...}})."""
        self._change(
            "POST",
            f"/Instance/{instance['instanceId']}",
            {"instanceProperties": {"instance": instance, **properties, "contentOperationType": 1}},
        )
