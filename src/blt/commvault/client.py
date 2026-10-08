"""The Commvault SDK's entry point: CommvaultClient.

A facade in the sense of sdk-primer's example: one object per connection,
with a named group of calls for each kind of thing the CommServe has -
`cv.jobs`, `cv.clients`, `cv.instances`, `cv.subclients`, `cv.sql`,
`cv.commcell`, `cv.plans`, `cv.storage`, `cv.credentials`. Each group is
a small class in its own module.

sdk-primer supplies what is underneath: APIClient for transport (retry
and backoff, typed exceptions, per-request log correlation) and
TokenExchangeAuth for the username/password login. What is *not* used
from it is BaseAPIModel/ResourceManager. Those model a resource with
save/load/find at a path; Commvault's API does not have that shape - its
job listing is a POST with the filter and paging in the body, its
answers nest the useful part under varying keys, and most failures come
back as a 200 with an error inside. So the groups here are written
directly against APIClient, sharing those conventions through
_base.Resource.

Only endpoints that have been run against a live CommServe (11 SP46) are
here. cvpysdk was the reference for request shapes; it is not a
dependency.
"""

from __future__ import annotations

import base64
import ssl
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from sdk_primer import APIClient, AuthenticationError, TokenExchangeAuth

from .auth import AccessTokenAuth, TokenSet
from .clients import Clients, Instances
from .commcell import CommCell, Credentials, Plans, Storage
from .jobs import Jobs
from .sql import Sql
from .subclients import Subclients

# Commvault answers in XML unless asked otherwise.
_JSON_HEADERS = {"Accept": "application/json"}


def _token_from_login(response: httpx.Response) -> str:
    """A rejected login is still a 200 from Commvault, with the reason in
    errList instead of a token - so it has to be turned into an error
    here rather than left to the status code."""
    body = response.json()
    token = body.get("token")
    if token:
        return str(token)
    errors = body.get("errList") or [{}]
    reason = errors[0].get("errLogMessage") or body.get("errorMessage") or "no token returned"
    raise AuthenticationError(f"Commvault login failed: {reason}")


class CommvaultClient:
    """One connection to one CommServe, and the way in to everything the
    SDK can ask it:

        with CommvaultClient(url, access_token=tokens) as cv:
            cv.commcell.info()
            for page in cv.jobs.iter_pages(lookup_seconds=3600): ...
            for client in cv.clients.list():
                cv.instances.list(client["clientId"])

    It is read-only unless built with allow_changes=True; the calls that
    alter a CommServe (starting or killing jobs, creating or deleting
    subclients and clients, ...) refuse to run otherwise.
    """

    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        *,
        access_token: str | TokenSet | None = None,
        on_token_renew: Callable[[TokenSet, dict[str, Any]], None] | None = None,
        verify_tls: bool = True,
        ca_bundle: Path | None = None,
        page_size: int = 500,
        timeout: float = 120.0,
        allow_changes: bool = False,
    ) -> None:
        base_url = base_url.rstrip("/")
        verify: bool | ssl.SSLContext = verify_tls
        if verify_tls and ca_bundle is not None:
            verify = ssl.create_default_context(cafile=str(ca_bundle))
        self.page_size = page_size
        # Off by default, and off for everything blt's collector does.
        # See _base.Resource._change.
        self.allow_changes = allow_changes

        auth: httpx.Auth
        # A second, plain client for the calls that *obtain* credentials
        # (login, token renewal) - they can't go through the client whose
        # auth they are in the middle of supplying.
        self._login_client = httpx.Client(verify=verify, headers=_JSON_HEADERS, timeout=timeout)
        if access_token:
            # An access token (Command Center > user > Access tokens) is
            # issued ahead of time, so there is no login call: it rides in
            # the Authtoken header, and is renewed with its refresh token
            # when it runs out (see auth.py).
            tokens = TokenSet(access_token) if isinstance(access_token, str) else access_token
            auth = AccessTokenAuth(
                base_url, tokens, client=self._login_client, on_renew=on_token_renew
            )
        elif username and password:
            # POST /Login with the password base64-encoded returns a token
            # ("QSDK ...") that goes, as-is, in the Authtoken header of
            # every later request. TokenExchangeAuth logs in on first use
            # and again whenever a request comes back 401 (it timed out).
            auth = TokenExchangeAuth(
                token_url=f"{base_url}/Login",
                credentials={
                    "username": username,
                    "password": base64.b64encode(password.encode()).decode(),
                },
                token_from_response=_token_from_login,
                auth_scheme=None,
                auth_header="Authtoken",
                client=self._login_client,
            )
        else:
            raise ValueError("Pass either access_token= or username= and password=.")
        self._api = APIClient(
            base_url=base_url,
            auth=auth,
            default_headers=_JSON_HEADERS,
            timeout=timeout,
            transport=httpx.HTTPTransport(verify=verify),
        )
        # A second transport for calls that change something, which never
        # retries. Reads are safe to repeat; "start a backup" is not. When
        # such a request times out, the CommServe may well have acted on
        # it already, and sending it again starts a second job (seen: an
        # overloaded CommServe, three attempts, extra jobs). Commvault
        # does not honour an idempotency key, so the only safe number of
        # attempts is one.
        self._api_once = APIClient(
            base_url=base_url,
            auth=auth,
            default_headers=_JSON_HEADERS,
            timeout=timeout,
            max_attempts=1,
            transport=httpx.HTTPTransport(verify=verify),
        )

        self.commcell = CommCell(self)
        self.jobs = Jobs(self)
        self.clients = Clients(self)
        self.instances = Instances(self)
        self.subclients = Subclients(self)
        self.sql = Sql(self)
        self.plans = Plans(self)
        self.storage = Storage(self)
        self.credentials = Credentials(self)

    def close(self) -> None:
        self._api.close()
        self._api_once.close()
        self._login_client.close()

    def __enter__(self) -> CommvaultClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def api_once(self) -> APIClient:
        """The transport for changing calls: one attempt, no retry."""
        return self._api_once

    @property
    def api(self) -> APIClient:
        """The authenticated transport itself, for an endpoint the SDK
        does not wrap yet. Prefer adding it to the right group."""
        return self._api
