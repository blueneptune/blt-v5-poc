"""CommCell-wide things: the CommServe itself, plans, storage, stored
credentials, and what is needed to install a new client."""

from __future__ import annotations

import base64
from typing import Any

from ._base import CommvaultError, Entities, Resource


class CommCell(Resource):
    def info(self) -> dict[str, Any]:
        """The CommServe's description of itself (GET /CommServ):
        hostName, csVersionInfo, commcell.commCellName, time zone. Needs
        a valid session and changes nothing, which makes it the cheapest
        way to prove the configured credentials work."""
        return self._get("/CommServ")

    def packages(self) -> Entities:
        """Client install packages the CommServe advertises
        (GET /V4/commcell/available-packages), each with a public
        downloadURL and a SHA-256 checksum."""
        packages: Entities = self._get("/V4/commcell/available-packages").get("pkgList") or []
        return packages

    def install_authcode(self) -> str:
        """A code that lets a client installer register without a user
        name and password. Asking for one turns authcode-based
        installation on for the whole CommCell."""
        data = self._change("POST", "/Organization/0/Authtoken")
        code = data.get("organizationProperties", {}).get("authCode")
        if not code:
            raise CommvaultError(f"No install authcode in the reply: {data}")
        return str(code)


class Plans(Resource):
    def list(self) -> Entities:
        """Plan summaries (GET /V4/Plan/Summary): plan.name, plan.id,
        planType, status, numberOfCopies."""
        plans: Entities = self._get("/V4/Plan/Summary").get("plans") or []
        return plans

    def ids(self) -> dict[str, int]:
        """Plan name -> id."""
        return {p["plan"]["name"]: p["plan"]["id"] for p in self.list()}


class Storage(Resource):
    def disk(self) -> Entities:
        """Disk storage pools (GET /V4/Storage/Disk): name, status,
        capacity and freeSpace in MB."""
        pools: Entities = self._get("/V4/Storage/Disk").get("diskStorage") or []
        return pools


class Credentials(Resource):
    def names(self) -> list[str]:
        """Names of the credentials stored in Credential Manager."""
        body = self._list("/CommCell/Credentials?propertyLevel=10")
        return [
            c.get("credentialRecord", {}).get("credentialName", "")
            for c in body.get("credentialRecordInfo") or []
        ]

    def create(self, name: str, user_name: str, password: str, description: str = "") -> None:
        """Store a user name and password under `name`. The password is
        sent base64-encoded, as Commvault expects, not in the clear."""
        self._change(
            "POST",
            "/Commcell/Credentials",
            {
                "credentialRecordInfo": [
                    {
                        "recordType": 1,
                        "description": description,
                        "credentialRecord": {"credentialName": name},
                        "record": {
                            "userName": user_name,
                            "password": base64.b64encode(password.encode()).decode(),
                        },
                    }
                ]
            },
        )
