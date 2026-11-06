"""The API image installs only the base dependencies and the `api` extra
(Containerfile: `uv sync --no-dev --extra api`). Every package blt.api
imports must come from those - one that only arrives with the dev or
prefect dependencies passes the tests and then crashes the image
(2026.11.06: httpx)."""

import ast
import importlib.metadata as md
import re
import sys
from pathlib import Path

from packaging.requirements import Requirement

API = Path(__file__).resolve().parents[1] / "src" / "blt" / "api"


def _name(text: str) -> str:
    return re.sub(r"[-_.]+", "-", text).lower()


def _imported_modules() -> set[str]:
    found: set[str] = set()
    for path in API.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module.split(".")[0])
    return {m for m in found if m not in sys.stdlib_module_names and m != "blt"}


def _installed_with(extras: set[str]) -> set[str]:
    """The distributions `pip install blt[extras]` brings in, transitively."""
    seen: set[str] = set()
    todo: list[tuple[str, set[str]]] = [("blt", extras)]
    while todo:
        dist, wanted = todo.pop()
        key = _name(dist)
        if key in seen and dist != "blt":
            continue
        seen.add(key)
        for line in md.requires(dist) or []:
            req = Requirement(line)
            if req.marker and not any(
                req.marker.evaluate({"extra": extra}) for extra in (wanted or {""})
            ):
                continue
            if _name(req.name) == "blt":
                continue
            todo.append((req.name, set(req.extras)))
    return seen


def test_everything_blt_api_imports_is_installed_in_the_api_image() -> None:
    image = _installed_with({"api"})
    providers = md.packages_distributions()
    missing = {
        module: providers.get(module, [module])
        for module in _imported_modules()
        if not any(_name(d) in image for d in providers.get(module, [module]))
    }
    assert not missing, f"imported by blt.api but not in the api extra: {missing}"
