"""`blt-rubrik` - Rubrik accounts in blt: add one, look at them, collect.

    blt-rubrik add <name> <service-account.json>   make the account's Prefect block
    blt-rubrik show [<name>...]                     what the blocks hold, secret hidden
    blt-rubrik collect <name> [--full]              one collection, from this host
    blt-rubrik collect <name> --service-account <file>
                                                    the same, without Prefect

`add` and `show` talk to Prefect (PREFECT_API_URL, as for
blt-prefect-block; `source scripts/use-cluster.sh` for the cluster).
`collect` needs blt's API: BLT_API_URL and BLT_API_KEY from the
environment or config/blt.env.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger
from rubrik_sdk import ServiceAccount
from sdk_primer import SDKError, configure_logging

if TYPE_CHECKING:
    from blt.flows.rubrik import RubrikAccountBlock


def _load(name: str) -> RubrikAccountBlock:
    from blt.flows.rubrik import RubrikAccountBlock

    # Block.load is typed as possibly a coroutine; called from sync code
    # it is not.
    return cast("RubrikAccountBlock", RubrikAccountBlock.load(name))


def add(name: str, file: Path, *, overwrite: bool) -> str:
    account = ServiceAccount.from_json_file(file)
    _ = account.base_url  # refuses a file that is not an RSC service account
    from blt.flows.rubrik import RubrikAccountBlock

    block = RubrikAccountBlock.from_service_account(account)
    block.save(name, overwrite=overwrite)
    return (
        f"{name}: saved - {account.base_url}, service account {account.name or '?'} "
        "(secret stored, not shown). The JSON file is no longer needed by blt; keep it "
        "somewhere safe or delete it."
    )


def summary(name: str, block: RubrikAccountBlock) -> str:
    secret = "set" if block.client_secret.get_secret_value() else "EMPTY"
    return (
        f"{name}: {block.service_account().base_url}  service account "
        f"{block.service_account_name or '?'}  secret {secret}  collect={block.collect}  "
        f"initial={block.initial_lookback_hours}h overlap={block.overlap_minutes}m "
        f"full={block.history_limit_days}d"
    )


def show(names: list[str]) -> list[str]:
    from blt.flows.rubrik import collectable_accounts

    return [summary(n, _load(n)) for n in names or collectable_accounts()]


def collect(name: str, *, full: bool, service_account: Path | None) -> str:
    from blt.flows.rubrik import RubrikAccountBlock, collect_account

    if service_account is not None:
        block = RubrikAccountBlock.from_service_account(
            ServiceAccount.from_json_file(service_account)
        )
    else:
        block = _load(name)
    return collect_account(name, block, force_full=full)


def main() -> None:
    parser = argparse.ArgumentParser(prog="blt-rubrik", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_add = sub.add_parser("add", help="make an account's Prefect block from its service account")
    p_add.add_argument("name")
    p_add.add_argument("file", type=Path)
    p_add.add_argument("--overwrite", action="store_true", help="replace an existing block")
    p_show = sub.add_parser("show", help="what the account blocks hold")
    p_show.add_argument("names", nargs="*")
    p_collect = sub.add_parser("collect", help="one collection run, from this host")
    p_collect.add_argument("name")
    p_collect.add_argument("--full", action="store_true", help="everything in the history limit")
    p_collect.add_argument(
        "--service-account", type=Path, help="use this file instead of the Prefect block"
    )
    args = parser.parse_args()

    configure_logging(level="INFO")
    try:
        if args.command == "add":
            print(add(args.name, args.file, overwrite=args.overwrite))
        elif args.command == "show":
            for line in show(args.names):
                print(line)
        else:
            print(collect(args.name, full=args.full, service_account=args.service_account))
    except (SDKError, OSError, ValueError) as exc:
        logger.error("{}: {}", type(exc).__name__, exc)
        sys.exit(1)
