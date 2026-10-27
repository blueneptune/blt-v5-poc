"""blt-prefect-import: move CommCells' .env files into blocks, and back.

    blt-prefect-import cv-toaster            # config/cv-toaster.env -> block
    blt-prefect-import --all                 # every config/*.env but blt.env
    blt-prefect-import --to-env cv-toaster   # the block's tokens -> the .env file

Prints what it did, never a value.

A CommCell's tokens may be used from one place only: renewing with a
copy that has gone stale was seen to make Commvault refuse the current
pair too. So importing moves them: the .env file's token lines are
blanked and BLT_TOKENS_IN=prefect put in their place, and collect.sh and
the other host tools refuse a CommCell marked so. --to-env moves them
back and removes the mark.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import dotenv_values

from blt.collector.tokens import (
    HELD_BY_KEY,
    TOKEN_KEYS,
    hand_over_tokens,
    tokens_held_by,
    update_env_file,
)

from .blocks import CommvaultCommCell

SHARED = "blt.env"


def env_names(config_dir: Path) -> list[str]:
    return sorted(
        path.stem
        for path in config_dir.glob("*.env")
        if path.name != SHARED and not path.name.startswith(".")
    )


def blocks_from_env(config_dir: Path, names: list[str]) -> dict[str, CommvaultCommCell]:
    """A block per CommCell file. A file another file names as its
    CV_CREDENTIALS (an MSP login) is imported with collect=False: it
    lends credentials and is not collected from itself."""
    values = {name: dotenv_values(config_dir / f"{name}.env") for name in names}
    # Every file in the directory, not just the ones being imported: an MSP
    # login imported on its own is still lent to the customers' files.
    lenders = {
        (dotenv_values(config_dir / f"{other}.env").get("CV_CREDENTIALS") or "").strip()
        for other in env_names(config_dir)
    } - {""}
    blocks = {}
    for name, env in values.items():
        block = CommvaultCommCell.from_env(dict(env))
        block.collect = name not in lenders
        blocks[name] = block
    return blocks


def describe(name: str, block: CommvaultCommCell) -> str:
    """What was imported, with every secret reduced to whether it is set."""
    parts = [f"deployment={block.deployment or '-'}", f"collect={block.collect}"]
    if block.credentials:
        parts.append(f"credentials from {block.credentials}")
    secrets = [f for f in ("access_token", "refresh_token", "password") if getattr(block, f)]
    parts.append(f"secrets: {', '.join(secrets) or 'none'}")
    if block.options:
        parts.append(f"options: {', '.join(sorted(block.options))}")
    return f"{name}: " + "; ".join(parts)


def tokens_to_env(name: str, config_dir: Path) -> None:
    block: CommvaultCommCell = CommvaultCommCell.load(name)  # type: ignore[assignment]
    path = config_dir / f"{name}.env"
    env = block.to_env()
    updates = {key: env.get(key, "") for key in TOKEN_KEYS}
    update_env_file(path, {**updates, HELD_BY_KEY: ""})
    print(
        f"{name}: tokens copied from the block to {path}. Delete the block, or turn its "
        "collect off, before using the file: one place only."
    )


def import_files(config_dir: Path, names: list[str], *, overwrite: bool = False) -> None:
    """Each named .env file into a block; the tokens move, not copy."""
    for name, block in blocks_from_env(config_dir, names).items():
        path = config_dir / f"{name}.env"
        holder = tokens_held_by(path)
        if holder and block.access_token is None:
            print(
                f"{name}: skipped - its tokens are already in {holder} ({HELD_BY_KEY} in "
                f"{path}). Change its settings with blt-prefect-block set."
            )
            continue
        block.save(name, overwrite=overwrite)
        print(describe(name, block))
        if block.access_token is not None:
            hand_over_tokens(path)
            print(f"{name}: tokens removed from {path}; they live in the block now")


def main() -> None:
    parser = argparse.ArgumentParser(prog="blt-prefect-import", description=__doc__.split("\n")[0])
    parser.add_argument("names", nargs="*", help="CommCell names (config/<name>.env)")
    parser.add_argument("--all", action="store_true", help="every config/*.env except blt.env")
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--overwrite", action="store_true", help="replace existing blocks")
    parser.add_argument(
        "--to-env", action="store_true", help="copy the named blocks' tokens back to .env files"
    )
    args = parser.parse_args()

    if args.to_env:
        for name in args.names:
            tokens_to_env(name, args.config_dir)
        return

    names = env_names(args.config_dir) if args.all else args.names
    if not names:
        parser.error("name one or more CommCells, or use --all")
    missing = [n for n in names if not (args.config_dir / f"{n}.env").is_file()]
    if missing:
        sys.exit(f"Not found: {', '.join(f'{args.config_dir}/{n}.env' for n in missing)}")

    import_files(args.config_dir, names, overwrite=args.overwrite)
    print("Done. Collect these CommCells from Prefect; --to-env moves tokens back to a file.")


if __name__ == "__main__":
    main()
