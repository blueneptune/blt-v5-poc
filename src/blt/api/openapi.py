"""`blt-openapi` - write the API's OpenAPI description to docs/openapi.json.

    blt-openapi            regenerate docs/openapi.json
    blt-openapi --check    exit 1 if it is out of date (what the test does)

The file is for reading the API without running it - on GitHub, or by
importing it into Postman, Insomnia or a client generator. A running API
serves the same thing at /openapi.json, with /docs and /redoc on top.
No database is needed: the description comes from the code alone.

The check ignores info.version, so bumping blt's version does not make
the file "out of date"; regenerate when you want the number in it right.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from .app import create_app
from .settings import ApiSettings

SPEC_PATH = Path(__file__).resolve().parents[3] / "docs" / "openapi.json"


def spec() -> dict[str, Any]:
    # create_app connects to nothing until a request needs the database.
    settings = ApiSettings(
        database_url=SecretStr("postgresql+psycopg://docs@localhost/docs"),
        api_key=SecretStr("docs"),
    )
    return create_app(settings).openapi()


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def up_to_date(path: Path) -> bool:
    if not path.exists():
        return False

    def unversioned(document: dict[str, Any]) -> dict[str, Any]:
        return {**document, "info": {**document["info"], "version": None}}

    return unversioned(json.loads(path.read_text())) == unversioned(spec())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="blt-openapi", description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="only check it is up to date")
    parser.add_argument("--out", type=Path, default=SPEC_PATH, help=f"default {SPEC_PATH}")
    args = parser.parse_args(argv)

    if args.check:
        if not up_to_date(args.out):
            print(f"{args.out} is out of date: run blt-openapi", file=sys.stderr)
            return 1
        return 0
    args.out.write_text(render(spec()))
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
