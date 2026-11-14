"""Reading a NetBackup primary's SLPs (blt.netbackup.slps)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from netbackup_sdk import NetBackupClient

from blt.netbackup.slps import netbackup_slps, slp_from


def test_an_slp_s_steps_name_their_storage_and_their_source() -> None:
    slp = slp_from(
        {
            "id": "slp-gold",
            "attributes": {
                "slpName": "slp-gold",
                "versionNumber": 4,
                "active": True,
                "operationList": [
                    {
                        "operationIndex": 2,
                        "operationType": "REPLICATE_TO_REMOTE_MASTER",
                        "storage": {"name": None},
                        "sourceOperationIndex": 1,
                        "targetMasterServer": "nbu-dr",
                        "targetImportSLP": "slp-import",
                    },
                    {"operationIndex": 1, "operationType": "BACKUP", "storage": {"name": "stu-a"}},
                ],
            },
        }
    )
    assert (slp.name, slp.version, slp.active) == ("slp-gold", 4, True)
    first, second = slp.operations
    assert (first.index, first.type, first.storage) == (1, "BACKUP", "stu-a")
    assert (second.storage, second.source_index, second.target_primary) == (None, 1, "nbu-dr")
    assert second.target_import_slp == "slp-import"


def test_an_slp_listed_without_its_steps_is_read_on_its_own() -> None:
    asked: list[str] = []

    def get_slp(name: str) -> dict[str, Any]:
        asked.append(name)
        step = {"operationIndex": 1, "operationType": "BACKUP", "storage": {"name": "stu"}}
        return {"data": {"id": name, "attributes": {"slpName": name, "operationList": [step]}}}

    nbu = SimpleNamespace(
        raw=SimpleNamespace(
            config=SimpleNamespace(
                list_slps=lambda page_limit, page_offset: {
                    "data": [{"id": "bare", "attributes": {"slpName": "bare"}}]
                },
                get_slp=get_slp,
            )
        )
    )
    (slp,) = netbackup_slps(cast(NetBackupClient, nbu))
    assert asked == ["bare"] and slp.operations[0].storage == "stu"
