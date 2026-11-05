"""`blt-netbackup` - NetBackup primaries in blt.

    blt-netbackup add <name> --url https://<primary>:1556/netbackup [--ca <pem file>]
                                             make the primary's Prefect block; the
                                             API key is read from NBU_API_KEY
    blt-netbackup show [<name>...]           what the blocks hold, key hidden
    blt-netbackup storage <name>             one storage collection, from this host

The API key comes from the environment, never the command line, so it
stays out of shell history and process lists. `add` and `show` talk to
Prefect (PREFECT_API_URL); `storage` also needs blt's API (BLT_API_URL
and BLT_API_KEY, from the environment or config/blt.env).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import SecretStr
from sdk_primer import SDKError, configure_logging

if TYPE_CHECKING:
    from blt.flows.netbackup import NetBackupPrimaryBlock


def _load(name: str) -> NetBackupPrimaryBlock:
    from blt.flows.netbackup import NetBackupPrimaryBlock

    return cast("NetBackupPrimaryBlock", NetBackupPrimaryBlock.load(name))


def add(name: str, url: str, ca: Path | None, *, overwrite: bool) -> str:
    key = os.environ.get("NBU_API_KEY", "")
    if not key:
        raise SystemExit("Set NBU_API_KEY to the primary's API key first.")
    from blt.flows.netbackup import NetBackupPrimaryBlock

    block = NetBackupPrimaryBlock(
        url=url,
        api_key=SecretStr(key),
        ca_certificate=ca.read_text() if ca else None,
    )
    block.save(name, overwrite=overwrite)
    return f"{name}: saved - {url} (API key stored, not shown{', CA certificate' if ca else ''})"


def summary(name: str, block: NetBackupPrimaryBlock) -> str:
    key = "set" if block.api_key.get_secret_value() else "EMPTY"
    ca = "CA certificate" if block.ca_certificate else "public CA"
    flags = f"verify_tls={block.verify_tls} collect={block.collect}"
    return f"{name}: {block.url}  key {key}  {ca}  {flags}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="blt-netbackup", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    p_add = commands.add_parser("add", help="make a primary's Prefect block")
    p_add.add_argument("name")
    p_add.add_argument("--url", required=True)
    p_add.add_argument("--ca", type=Path, help="the primary's CA certificate, PEM")
    p_add.add_argument("--overwrite", action="store_true")
    p_show = commands.add_parser("show", help="what the blocks hold")
    p_show.add_argument("names", nargs="*")
    p_storage = commands.add_parser("storage", help="one storage collection")
    p_storage.add_argument("name")
    args = parser.parse_args(argv)
    configure_logging()

    if args.command == "add":
        print(add(args.name, args.url, args.ca, overwrite=args.overwrite))
        return 0
    if args.command == "show":
        names = args.names
        if not names:
            from blt.flows.netbackup import collectable_primaries

            names = collectable_primaries()
        for name in names:
            print(summary(name, _load(name)))
        return 0
    from blt.flows.netbackup import storage_primary

    try:
        print(storage_primary(args.name, _load(args.name)))
    except SDKError as exc:
        print(f"{args.name}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
