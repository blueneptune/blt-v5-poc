"""SQL Server: what Commvault knows about databases on an instance."""

from __future__ import annotations

from typing import Any

from ._base import Entities, Resource

APP_NAME = "SQL Server"


class Sql(Resource):
    def databases(self, instance_id: int) -> Entities:
        """The databases Commvault has backed up on an instance
        (GET /sql/databases?instance=). Per database: dbName, bkpTime
        (last backup), jobId (last backup job), fullJobId (last full),
        bkpSize, rModel, planName.

        User databases only - master, model and msdb are backed up but
        never listed here - and only ones a backup has already reached:
        a database created since the last backup is not yet known."""
        databases: Entities = (
            self._list(f"/sql/databases?instance={instance_id}").get("SqlDatabase") or []
        )
        return databases

    def discovered(self, subclient_properties: dict[str, Any]) -> Entities:
        """The databases named in a SQL subclient's content - what
        Commvault's own discovery has seen, system databases included.
        Takes the result of subclients.get()."""
        return [
            item["mssqlDbContent"]
            for item in subclient_properties.get("content") or []
            if item.get("mssqlDbContent", {}).get("databaseName")
        ]

    def set_instance_credential(self, instance: dict[str, Any], credential_name: str) -> None:
        """Make an instance log in to SQL Server with a stored credential.
        Required on Linux, where the agent cannot use a local system
        account: it needs a SQL-authenticated sysadmin login."""
        self._client.instances.update(
            instance,
            {
                "mssqlInstance": {
                    "overrideHigherLevelSettings": {
                        "overrideGlobalAuthentication": True,
                        "useLocalSystemAccount": False,
                    },
                    "MSSQLCredentialinfo": {"credentialName": credential_name},
                }
            },
        )
