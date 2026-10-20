"""blt-prefect-block: look at and change CommCell blocks without the
Prefect UI's edit page.

    blt-prefect-block show                          # every CommCell block
    blt-prefect-block show cv-toaster
    blt-prefect-block set cv-toaster base_url=https://cv-toaster.corp.example.com/commandcenter/api
    blt-prefect-block set acme collect=false CV_PAGE_SIZE=1000
    blt-prefect-block tokens-from-env cv-toaster    # credentials from config/cv-toaster.env
    blt-prefect-block tokens-from-env --all

Each change loads the block with its secrets, changes only what was
named, and saves it back, so nothing else in the block is touched. (The
UI's edit page has been seen to come up empty for a block and, if saved,
to wipe its tokens.) Prints no secret values.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, cast

from dotenv import dotenv_values
from prefect.client.orchestration import get_client
from pydantic import ValidationError

from .blocks import BLOCK_TYPE_SLUG, CommvaultCommCell
from .importer import env_names

# Fields `set` may change. The secrets are not among them: they come from
# a .env file (tokens-from-env), so they never pass through a command line.
SETTABLE = (
    "base_url",
    "deployment",
    "collect",
    "credentials",
    "account_id",
    "tenant_guid",
    "ring_id",
    "verify_tls",
    "username",
)
CREDENTIALS = (
    "access_token",
    "refresh_token",
    "token_expires_at",
    "token_renewable_until",
    "username",
    "password",
)


def load(name: str) -> CommvaultCommCell:
    return cast(CommvaultCommCell, CommvaultCommCell.load(name))


def all_names() -> list[str]:
    with get_client(sync_client=True) as client:
        documents = client.read_block_documents_by_type(BLOCK_TYPE_SLUG, include_secrets=False)
    return sorted(d.name for d in documents if d.name)


def summary(name: str, block: CommvaultCommCell) -> str:
    def shown(value: object) -> str:
        return "-" if value is None else str(value)

    secrets = ", ".join(
        f"{field} {'set' if getattr(block, field) else 'EMPTY'}"
        for field in ("access_token", "refresh_token")
    )
    lines = [
        f"{name}",
        f"  collect={block.collect}  deployment={shown(block.deployment)}  "
        f"credentials={shown(block.credentials)}",
        f"  base_url={shown(block.base_url)}",
        f"  account_id={shown(block.account_id)}  tenant_guid={shown(block.tenant_guid)}",
        f"  {secrets}; expires {shown(block.token_expires_at)}, "
        f"renewable until {shown(block.token_renewable_until)}",
    ]
    if block.options:
        lines.append(
            "  options: " + ", ".join(f"{k}={v}" for k, v in sorted(block.options.items()))
        )
    return "\n".join(lines)


def apply_settings(block: CommvaultCommCell, assignments: list[str]) -> list[str]:
    """Apply field=value (and CV_*=value into options); returns what changed."""
    changed = []
    for assignment in assignments:
        field, sep, value = assignment.partition("=")
        if not sep:
            raise ValueError(f"'{assignment}' is not field=value")
        if field.startswith("CV_"):
            if value:
                block.options[field] = value
            else:
                block.options.pop(field, None)
        elif field in SETTABLE:
            parsed: Any = value or None
            if field in ("collect", "verify_tls") and value:
                parsed = value.lower() not in ("false", "0", "no")
            setattr(block, field, parsed)
        else:
            raise ValueError(
                f"'{field}' cannot be set here. Settable: {', '.join(SETTABLE)}, or any CV_* "
                "option. Credentials come from the .env file: use tokens-from-env."
            )
        changed.append(field)
    # Validate the result as a whole before anything is saved.
    CommvaultCommCell.model_validate(block.model_dump())
    return changed


def credentials_from_env(block: CommvaultCommCell, env_file: Path) -> list[str]:
    """Copy the credentials an .env file holds into a block; returns which."""
    from_file = CommvaultCommCell.from_env(dict(dotenv_values(env_file)))
    copied = []
    for field in CREDENTIALS:
        value = getattr(from_file, field)
        if value is not None:
            setattr(block, field, value)
            copied.append(field)
    return copied


def main() -> None:
    parser = argparse.ArgumentParser(prog="blt-prefect-block", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show", help="what blocks hold, secrets shown only as set or EMPTY")
    show.add_argument("names", nargs="*")
    put = sub.add_parser("set", help="change fields of one block")
    put.add_argument("name")
    put.add_argument("assignments", nargs="+", metavar="field=value")
    tokens = sub.add_parser("tokens-from-env", help="credentials from .env files into blocks")
    tokens.add_argument("names", nargs="*")
    tokens.add_argument("--all", action="store_true")
    tokens.add_argument("--config-dir", type=Path, default=Path("config"))
    args = parser.parse_args()

    try:
        if args.command == "show":
            for name in args.names or all_names():
                print(summary(name, load(name)))
        elif args.command == "set":
            block = load(args.name)
            changed = apply_settings(block, args.assignments)
            block.save(args.name, overwrite=True)
            print(f"{args.name}: changed {', '.join(changed)}")
            print(summary(args.name, block))
        else:
            names = env_names(args.config_dir) if args.all else args.names
            if not names:
                parser.error("name one or more CommCells, or use --all")
            for name in names:
                env_file = args.config_dir / f"{name}.env"
                if not env_file.is_file():
                    print(f"{name}: no {env_file}, skipped")
                    continue
                block = load(name)
                copied = credentials_from_env(block, env_file)
                if copied:
                    block.save(name, overwrite=True)
                print(f"{name}: {', '.join(copied) if copied else 'no credentials in the file'}")
    except (ValueError, ValidationError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
