"""Subclients: what a backup of a client is defined to cover."""

from __future__ import annotations

from typing import Any

from ._base import CommvaultError, Entities, Resource


class Subclients(Resource):
    def list(self, client_id: int) -> Entities:
        """Each subclient's entity on a client (GET /Subclient):
        subclientId, subclientName, instanceId, appName, backupsetName."""
        body = self._list(f"/Subclient?clientId={client_id}")
        return [
            s["subClientEntity"]
            for s in body.get("subClientProperties") or []
            if "subClientEntity" in s
        ]

    def get(self, subclient_id: int) -> dict[str, Any]:
        """One subclient's full properties (GET /Subclient/{id}),
        including its content and its plan."""
        properties = self._list(f"/Subclient/{subclient_id}").get("subClientProperties") or [{}]
        first: dict[str, Any] = properties[0]
        return first

    # -- changes -----------------------------------------------------------

    def create(
        self,
        client_name: str,
        name: str,
        *,
        content: Entities,
        plan: dict[str, Any] | None = None,
        description: str = "",
        app_name: str = "File System",
        instance_name: str = "DefaultInstanceName",
        backupset_name: str = "defaultBackupSet",
    ) -> None:
        """Create a subclient. `content` is Commvault's content list, e.g.
        [{"path": "/data"}]; `plan` is {"planName": ..., "planId": ...}."""
        properties: dict[str, Any] = {
            "contentOperationType": 2,
            "subClientEntity": {
                "clientName": client_name,
                "appName": app_name,
                "instanceName": instance_name,
                "backupsetName": backupset_name,
                "subclientName": name,
            },
            "content": content,
            "commonProperties": {"enableBackup": True, "description": description},
        }
        if plan:
            properties["planEntity"] = plan
        self._change("POST", "/Subclient", {"subClientProperties": properties})

    def set_plan(self, subclient_id: int, plan: dict[str, Any]) -> None:
        self._change(
            "POST", f"/Subclient/{subclient_id}", {"subClientProperties": {"planEntity": plan}}
        )

    def backup(self, subclient_id: int, level: str = "Incremental") -> int:
        """Start a backup and return its job id. Levels seen to work:
        Full, Incremental, Differential, Transaction_Log.

        Commvault answers 409 if the subclient already has a backup
        queued or running; that surfaces as sdk_primer.ClientError."""
        data = self._change("POST", f"/Subclient/{subclient_id}/action/backup?backupLevel={level}")
        job_ids = data.get("jobIds") or []
        if not job_ids:
            raise CommvaultError(
                f"{level} backup of subclient {subclient_id} did not start: {data}"
            )
        return int(job_ids[0])

    def delete(self, subclient_id: int) -> None:
        self._change("DELETE", f"/Subclient/{subclient_id}")
