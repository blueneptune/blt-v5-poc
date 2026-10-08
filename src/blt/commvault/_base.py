"""What every resource group in the SDK shares: how a call is made, how
Commvault's answers are read, and the rule about changes.

Three kinds of call, and the difference matters:

- `_get` / `_list`  - an HTTP GET.
- `_query`          - an HTTP POST that only *asks* (Commvault puts some
                      filters in a request body: the job listing, job
                      details). Safe.
- `_change`         - anything that alters the CommServe. Refused unless
                      the client was built with allow_changes=True.

blt's collector never needs `_change`, and is built without it, so a bug
or a wrong call there cannot modify a production CommServe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from sdk_primer import NotFoundError, SDKError, ServerError

if TYPE_CHECKING:
    from .client import CommvaultClient


# Commvault's own JSON for one thing, and a listing of them. Spelled out
# as names because several groups have a method called `list`, which
# would otherwise shadow the built-in in their own annotations.
Entity = dict[str, Any]
Entities = list[Entity]
Names = list[str]


class CommvaultError(SDKError):
    """Commvault answered, and the answer was a refusal. It reports most
    failures as an HTTP 200 with an error inside the body, so these never
    surface as HTTP errors."""


class ChangesNotAllowed(CommvaultError):
    """A call that would alter the CommServe was made on a client that
    was not built with allow_changes=True."""


def json_body(response: httpx.Response) -> dict[str, Any]:
    """The response body as a JSON object. While its web tier is
    restarting a CommServe answers every URL with a 200 and an HTML
    "Scheduled Maintenance" page - a failure that looks like success
    until something tries to parse it."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise ServerError(
            f"{response.request.method} {response.request.url} returned "
            f"{response.headers.get('content-type', 'no content type')} instead of JSON - "
            "the CommServe may be in maintenance or still starting."
        )
    return body


def error_in(data: dict[str, Any]) -> str | None:
    """The error Commvault tucked into a 200 response, if any. Where it
    puts one varies by endpoint; this checks the places seen so far."""
    candidates: list[dict[str, Any]] = [data]
    for key in ("response", "errorList", "errList", "error"):
        value = data.get(key)
        if isinstance(value, list):
            candidates.extend(v for v in value if isinstance(v, dict))
        elif isinstance(value, dict):
            candidates.append(value)
    for item in candidates:
        code = item.get("errorCode", 0)
        if code not in (0, "0", None):
            text = item.get("errorString") or item.get("errorMessage") or item.get("errLogMessage")
            return f"error {code}: {text or item}"
    return None


class Resource:
    """One group of related endpoints (jobs, clients, ...), reached as an
    attribute of CommvaultClient."""

    def __init__(self, client: CommvaultClient) -> None:
        self._client = client

    def _get(self, path: str) -> dict[str, Any]:
        return json_body(self._client.api.get(path))

    def _list(self, path: str) -> dict[str, Any]:
        """A listing. Commvault answers some "there are none" listings
        with a 404 instead of an empty list."""
        try:
            return self._get(path)
        except NotFoundError:
            return {}

    def _query(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return json_body(self._client.api.post(path, json=body))

    def _change(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._client.allow_changes:
            raise ChangesNotAllowed(
                f"{method} {path} would change the CommServe; this client is read-only. "
                "Build it with allow_changes=True if that is really intended."
            )
        # One attempt only - see CommvaultClient.api_once for why.
        data = json_body(self._client.api_once.request(method, path, json=body))
        problem = error_in(data)
        if problem:
            raise CommvaultError(f"{method} {path}: {problem}")
        return data
