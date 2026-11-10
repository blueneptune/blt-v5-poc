"""`blt-api-collections` - ready-made imports of blt's API for Bruno,
Postman and Insomnia, made from docs/openapi.json (TODO 22).

    blt-api-collections            regenerate docs/api-tools/
    blt-api-collections --check    exit 1 if they are out of date (what the test does)

Each tool gets every endpoint, in folders by the API's tags, plus a
**Common tasks** folder of ready-to-send requests (failed jobs of the last
day, run health, checking a source, ...), and an environment per install
- `desktop`, `lab`, `customer` - whose `apiKey` is left empty: fill it in
in the tool, never in a file here. Made from the committed spec, like it,
so they cannot drift: the test fails when the API changes and these were
not regenerated.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from .openapi import SPEC_PATH, render

OUT = SPEC_PATH.parent / "api-tools"

# Where each install's API answers. The key is never here.
ENVIRONMENTS = {
    "desktop": "http://127.0.0.1:8088",
    "lab": "https://api.blt.lab.ethos.home",
    "customer": "https://api.blt.example.com",
}

# Ready-to-send requests for the everyday tasks: (name, method, path,
# query, body, what it is for).
COMMON: list[tuple[str, str, str, dict[str, str], dict[str, Any] | None, str]] = [
    (
        "Failed jobs, last 24 hours",
        "GET",
        "/v1/jobs",
        {"state": "failed", "since": "{{since}}", "limit": "100"},
        None,
        "Every product's failed jobs since `since` (an ISO time, e.g. 2026-11-10T00:00:00Z).",
    ),
    (
        "Jobs still running",
        "GET",
        "/v1/jobs",
        {"active": "true", "limit": "100"},
        None,
        "Every job still running, any product.",
    ),
    (
        "Job counts by state, per source",
        "GET",
        "/v1/jobs/summary",
        {"group": "source", "since": "{{since}}"},
        None,
        "How many jobs ended each way, per source, since `since`.",
    ),
    (
        "Storage, with use and trend",
        "GET",
        "/v1/storage",
        {},
        None,
        "Every storage target with its latest use.",
    ),
    (
        "Sources and collectors",
        "GET",
        "/v1/admin/sources",
        {},
        None,
        "Every source, its collector, its timing and next starts; secrets only as set or not.",
    ),
    (
        "Run health",
        "GET",
        "/v1/admin/runs/health",
        {},
        None,
        "Are runs getting through: slots taken against runs collecting, and stuck runs.",
    ),
    (
        "Check a source",
        "POST",
        "/v1/admin/sources/{{sourceType}}/{{sourceName}}/check",
        {},
        None,
        "Starts the source's check on its collector; follow it with GET /v1/admin/runs/{id}.",
    ),
    (
        "Collect from a source now",
        "PATCH",
        "/v1/admin/sources/{{sourceType}}/{{sourceName}}",
        {},
        {"collect": True},
        "Turns collection on for a source (false: off).",
    ),
]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "request"


def _stable_id(*parts: str) -> str:
    """Ids that stay the same from one generation to the next."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "blt-api-collections:" + "/".join(parts)))


def _resolve(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if ref:
        name = ref.rsplit("/", 1)[-1]
        return spec["components"]["schemas"][name]  # type: ignore[no-any-return]
    return schema


def example_body(spec: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any] | None:
    """The request body's own example, or a skeleton of its required
    fields; None for an operation with no JSON body."""
    content = (operation.get("requestBody") or {}).get("content", {}).get("application/json")
    if not content:
        return None
    if "example" in content:
        return content["example"]  # type: ignore[no-any-return]
    schema = _resolve(spec, content.get("schema") or {})
    examples = schema.get("examples")
    if isinstance(examples, list) and examples and isinstance(examples[0], dict):
        return examples[0]  # type: ignore[no-any-return]
    body: dict[str, Any] = {}
    for name in schema.get("required", []):
        field = schema.get("properties", {}).get(name, {})
        given = field.get("examples")
        body[name] = given[0] if isinstance(given, list) and given else ""
    return body


class Request:
    """One request, as every tool needs it."""

    def __init__(
        self,
        folder: str,
        name: str,
        method: str,
        path: str,
        query: dict[str, str],
        body: dict[str, Any] | None,
        doc: str,
    ) -> None:
        self.folder = folder
        self.name = name
        self.method = method.upper()
        # {param} in the spec; {{param}} is every tool's variable.
        self.path = re.sub(r"(?<!\{)\{(\w+)\}(?!\})", r"{{\1}}", path)
        self.query = query
        self.body = body
        self.doc = doc

    @property
    def url(self) -> str:
        tail = "&".join(f"{k}={v}" for k, v in self.query.items())
        return "{{baseUrl}}" + self.path + (f"?{tail}" if tail else "")

    @property
    def variables(self) -> list[str]:
        return re.findall(r"\{\{(\w+)\}\}", self.path)


def requests(spec: dict[str, Any]) -> list[Request]:
    found = [
        Request("Common tasks", name, method, path, query, body, doc)
        for name, method, path, query, body, doc in COMMON
    ]
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            tag = (operation.get("tags") or ["other"])[0]
            query = {
                p["name"]: ""
                for p in operation.get("parameters", [])
                if p["in"] == "query" and p.get("required")
            }
            doc = operation.get("description") or operation.get("summary") or ""
            found.append(
                Request(
                    tag,
                    operation.get("summary") or f"{method.upper()} {path}",
                    method,
                    path,
                    query,
                    example_body(spec, operation),
                    doc.strip(),
                )
            )
    return found


def _folders(found: list[Request]) -> dict[str, list[Request]]:
    folders: dict[str, list[Request]] = {}
    for request in found:
        folders.setdefault(request.folder, []).append(request)
    return folders


def _no_key(request: Request) -> bool:
    return request.path == "/healthz"


# --- Postman (collection format 2.1) ----------------------------------------


def postman(spec: dict[str, Any], found: list[Request]) -> dict[str, Any]:
    def item(request: Request) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "name": request.name,
            "request": {
                "method": request.method,
                "header": [],
                "url": {
                    "raw": request.url,
                    "host": ["{{baseUrl}}"],
                    "path": [p for p in request.path.split("/") if p],
                    "query": [{"key": k, "value": v} for k, v in request.query.items()],
                },
                "description": request.doc,
            },
        }
        if _no_key(request):
            entry["request"]["auth"] = {"type": "noauth"}
        if request.body is not None:
            entry["request"]["header"].append({"key": "Content-Type", "value": "application/json"})
            entry["request"]["body"] = {"mode": "raw", "raw": json.dumps(request.body, indent=2)}
        return entry

    return {
        "info": {
            "_postman_id": _stable_id("postman"),
            "name": "blt API",
            "description": spec["info"].get("description", ""),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "auth": {
            "type": "apikey",
            "apikey": [
                {"key": "key", "value": "X-API-Key", "type": "string"},
                {"key": "value", "value": "{{apiKey}}", "type": "string"},
                {"key": "in", "value": "header", "type": "string"},
            ],
        },
        "item": [
            {"name": folder, "item": [item(r) for r in requests_]}
            for folder, requests_ in _folders(found).items()
        ],
    }


def postman_environment(name: str, base_url: str) -> dict[str, Any]:
    return {
        "id": _stable_id("postman-env", name),
        "name": f"blt {name}",
        "values": [
            {"key": "baseUrl", "value": base_url, "type": "default", "enabled": True},
            {"key": "apiKey", "value": "", "type": "secret", "enabled": True},
            {"key": "since", "value": "2026-01-01T00:00:00Z", "type": "default", "enabled": True},
            {"key": "sourceType", "value": "netbackup", "type": "default", "enabled": True},
            {"key": "sourceName", "value": "", "type": "default", "enabled": True},
        ],
    }


# --- Insomnia (export format 4, which Insomnia still imports) --------------


def insomnia(spec: dict[str, Any], found: list[Request]) -> dict[str, Any]:
    workspace = "wrk_" + _stable_id("insomnia")[:24].replace("-", "")
    base_env = "env_" + _stable_id("insomnia-base")[:24].replace("-", "")
    resources: list[dict[str, Any]] = [
        {"_id": workspace, "_type": "workspace", "name": "blt API", "description": ""},
        {
            "_id": base_env,
            "_type": "environment",
            "parentId": workspace,
            "name": "Base Environment",
            "data": {
                "baseUrl": ENVIRONMENTS["desktop"],
                "apiKey": "",
                "since": "2026-01-01T00:00:00Z",
                "sourceType": "netbackup",
                "sourceName": "",
            },
        },
    ]
    for name, base_url in ENVIRONMENTS.items():
        resources.append(
            {
                "_id": "env_" + _stable_id("insomnia-env", name)[:24].replace("-", ""),
                "_type": "environment",
                "parentId": base_env,
                "name": f"blt {name}",
                "data": {"baseUrl": base_url, "apiKey": ""},
            }
        )
    for folder, requests_ in _folders(found).items():
        group = "fld_" + _stable_id("insomnia-folder", folder)[:24].replace("-", "")
        resources.append(
            {"_id": group, "_type": "request_group", "parentId": workspace, "name": folder}
        )
        for request in requests_:
            entry: dict[str, Any] = {
                "_id": "req_"
                + _stable_id("insomnia-req", request.method, request.path, request.name)[
                    :24
                ].replace("-", ""),
                "_type": "request",
                "parentId": group,
                "name": request.name,
                "description": request.doc,
                "method": request.method,
                "url": "{{ _.baseUrl }}" + _insomnia_vars(request.path),
                "parameters": [
                    {"name": k, "value": _insomnia_vars(v)} for k, v in request.query.items()
                ],
                "headers": []
                if _no_key(request)
                else [{"name": "X-API-Key", "value": "{{ _.apiKey }}"}],
                "body": {},
            }
            if request.body is not None:
                entry["headers"].append({"name": "Content-Type", "value": "application/json"})
                entry["body"] = {
                    "mimeType": "application/json",
                    "text": json.dumps(request.body, indent=2),
                }
            resources.append(entry)
    return {
        "_type": "export",
        "__export_format": 4,
        "__export_source": "blt-api-collections",
        "resources": resources,
    }


def _insomnia_vars(text: str) -> str:
    return re.sub(r"\{\{(\w+)\}\}", r"{{ _.\1 }}", text)


# --- Bruno (a folder of .bru files) -------------------------------------------


def _bru_block(name: str, values: dict[str, str]) -> str:
    if not values:
        return ""
    lines = "\n".join(f"  {k}: {v}" for k, v in values.items())
    return f"{name} {{\n{lines}\n}}\n"


def _bru_text(name: str, text: str) -> str:
    body = "\n".join(f"  {line}" if line else "" for line in text.splitlines())
    return f"{name} {{\n{body}\n}}\n"


def bruno(found: list[Request]) -> dict[str, str]:
    """Relative path -> file content."""
    files: dict[str, str] = {
        "bruno.json": json.dumps(
            {"version": "1", "name": "blt API", "type": "collection", "ignore": []}, indent=2
        )
        + "\n",
        "collection.bru": _bru_block("auth", {"mode": "apikey"})
        + "\n"
        + _bru_block(
            "auth:apikey", {"key": "X-API-Key", "value": "{{apiKey}}", "placement": "header"}
        ),
    }
    for name, base_url in ENVIRONMENTS.items():
        files[f"environments/{name}.bru"] = (
            _bru_block(
                "vars",
                {
                    "baseUrl": base_url,
                    "since": "2026-01-01T00:00:00Z",
                    "sourceType": "netbackup",
                    "sourceName": "",
                },
            )
            + "vars:secret [\n  apiKey\n]\n"
        )
    for folder, requests_ in _folders(found).items():
        directory = _slug(folder)
        files[f"{directory}/folder.bru"] = _bru_block("meta", {"name": folder})
        names: set[str] = set()
        for seq, request in enumerate(requests_, start=1):
            stem = _slug(request.name)
            while stem in names:
                stem += "-2"
            names.add(stem)
            kind = "json" if request.body is not None else "none"
            parts = [
                _bru_block("meta", {"name": request.name, "type": "http", "seq": str(seq)}),
                _bru_block(
                    request.method.lower(),
                    {
                        "url": request.url,
                        "body": kind,
                        "auth": "none" if _no_key(request) else "inherit",
                    },
                ),
                _bru_block("params:query", request.query),
            ]
            if request.body is not None:
                parts.append(_bru_text("body:json", json.dumps(request.body, indent=2)))
            if request.doc:
                parts.append(_bru_text("docs", request.doc))
            files[f"{directory}/{stem}.bru"] = "\n".join(p for p in parts if p)
    return files


# --- all of them ---------------------------------------------------------------


def generate(spec: dict[str, Any]) -> dict[str, str]:
    """Relative path under docs/api-tools -> content."""
    found = requests(spec)
    files = {
        "postman/blt.postman_collection.json": render(postman(spec, found)),
        "insomnia/blt.insomnia.json": render(insomnia(spec, found)),
        **{f"bruno/{path}": text for path, text in bruno(found).items()},
    }
    for name, base_url in ENVIRONMENTS.items():
        files[f"postman/blt-{name}.postman_environment.json"] = render(
            postman_environment(name, base_url)
        )
    return files


def stale(out: Path, files: dict[str, str]) -> list[str]:
    """What differs between `files` and what is in `out`: missing, changed
    or left over."""
    found = (
        {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()} if out.exists() else set()
    )
    problems = [
        p for p, text in files.items() if not (out / p).exists() or (out / p).read_text() != text
    ]
    problems += sorted(p for p in found - set(files) if p != "README.md")
    return problems


def write(out: Path, files: dict[str, str]) -> None:
    for path in stale(out, files):
        target = out / path
        if path in files:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(files[path])
        else:
            target.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="blt-api-collections", description=(__doc__ or "").split("\n")[0]
    )
    parser.add_argument("--check", action="store_true", help="only check they are up to date")
    parser.add_argument("--out", type=Path, default=OUT, help=f"default {OUT}")
    args = parser.parse_args(argv)
    files = generate(json.loads(SPEC_PATH.read_text()))
    if args.check:
        problems = stale(args.out, files)
        if problems:
            print(
                f"{args.out} is out of date ({len(problems)} files): run blt-api-collections",
                file=sys.stderr,
            )
            return 1
        return 0
    write(args.out, files)
    print(f"Wrote {len(files)} files under {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
