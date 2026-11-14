"""A NetBackup primary's storage lifecycle policies (GET /config/slps):
for each, its steps - the storage each writes to, and the step it
copies from. Collected with the primary's storage, so the Storage page
can trace a copy job (whose policy is its SLP) back to the storage it
read, when the job itself does not say (blt.api.storage_flows).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from netbackup_sdk import NetBackupClient
from sdk_primer import APIClient

from blt.netbackup.schemas import NetBackupSlpBatch, NetBackupSlpIn, NetBackupSlpOperation
from blt.storage.netbackup import _all


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text(value: Any) -> str | None:
    return str(value) if value not in (None, "", "null") else None


def slp_from(item: dict[str, Any]) -> NetBackupSlpIn:
    """One SLP from NetBackup's JSON:API answer."""
    attributes = item.get("attributes") or {}
    operations = []
    for op in attributes.get("operationList") or []:
        index = _int(op.get("operationIndex"))
        if index is None:
            continue
        operations.append(
            NetBackupSlpOperation(
                index=index,
                type=str(op.get("operationType") or "UNKNOWN"),
                storage=_text((op.get("storage") or {}).get("name")),
                source_index=_int(op.get("sourceOperationIndex")),
                target_primary=_text(op.get("targetMasterServer")),
                target_import_slp=_text(op.get("targetImportSLP")),
            )
        )
    return NetBackupSlpIn(
        name=str(attributes.get("slpName") or item.get("id")),
        version=_int(attributes.get("versionNumber")),
        active=attributes.get("active") if isinstance(attributes.get("active"), bool) else None,
        operations=sorted(operations, key=lambda o: o.index),
    )


def netbackup_slps(nbu: NetBackupClient) -> list[NetBackupSlpIn]:
    slps = []
    for item in _all(nbu.raw.config.list_slps):
        if not (item.get("attributes") or {}).get("operationList"):
            # Should the list leave the steps out, the SLP itself has them.
            item = (nbu.raw.config.get_slp(str(item.get("id"))) or {}).get("data") or item
        slps.append(slp_from(item))
    return slps


def post_slps(
    client: APIClient, primary: str, collected_at: datetime, slps: list[NetBackupSlpIn]
) -> dict[str, int]:
    batch = NetBackupSlpBatch(collected_at=collected_at, slps=slps)
    response = client.post(
        f"/v1/sources/netbackup/{primary}/slps", json=batch.model_dump(mode="json")
    )
    result: dict[str, int] = response.json()
    return result
