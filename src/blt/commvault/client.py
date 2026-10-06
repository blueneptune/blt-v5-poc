"""A small Commvault REST client for job collection, built on sdk-primer.

sdk-primer's APIClient supplies the transport (retry/backoff, typed
exceptions, per-request log correlation) and TokenExchangeAuth supplies
the username/password login flow. What is *not* used is BaseAPIModel/ResourceManager: those
model a CRUD resource at a path, and Commvault's job listing is a POST
with a filter document in the body and its paging inside that body - so
the two calls collection needs are written directly against APIClient
here. cvpysdk (JobController._get_jobs_request_json) is the reference for
the request shape; it isn't a dependency.
"""

from __future__ import annotations

import base64
import ssl
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from sdk_primer import (
    APIClient,
    AuthenticationError,
    NotFoundError,
    ServerError,
    TokenExchangeAuth,
)

from blt.schemas import JobIn

from .auth import AccessTokenAuth, TokenSet
from .models import job_in_from_summary

# Commvault answers in XML unless asked otherwise.
_JSON_HEADERS = {"Accept": "application/json"}

# POST /Jobs "category": 0 = all, 1 = active only, 2 = finished only.
_CATEGORY_ALL = 0


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


def _json(response: httpx.Response) -> dict[str, Any]:
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


class CommvaultClient:
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
    ) -> None:
        base_url = base_url.rstrip("/")
        verify: bool | ssl.SSLContext = verify_tls
        if verify_tls and ca_bundle is not None:
            verify = ssl.create_default_context(cafile=str(ca_bundle))
        self.page_size = page_size

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

    def close(self) -> None:
        self._api.close()
        self._login_client.close()

    def __enter__(self) -> CommvaultClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def api(self) -> APIClient:
        """The authenticated transport itself, for callers that need a
        Commvault endpoint this class doesn't wrap (the lab rig under
        lab/ uses it). Collection goes through the methods below."""
        return self._api

    def commserve_info(self) -> dict[str, Any]:
        """GET /CommServ - the CommServe's own description of itself. It
        needs a valid session and changes nothing, which makes it the
        cheapest way to prove the configured credentials work."""
        return _json(self._api.get("/CommServ"))

    def iter_job_pages(self, lookup_seconds: int) -> Iterator[list[JobIn]]:
        """Every job Commvault will report, one page at a time: all jobs
        currently active, plus every job that finished within the last
        `lookup_seconds` (aged jobs included, so a first run reaches as
        far back as the CommServe still has history for).

        Paged in job-id order. New jobs get higher ids, so ones started
        while this is running land on later pages instead of shifting
        earlier ones.
        """
        offset = 0
        while True:
            payload: dict[str, Any] = {
                "scope": 1,
                "category": _CATEGORY_ALL,
                "pagingConfig": {
                    "sortField": "jobId",
                    "sortDirection": 1,
                    "offset": offset,
                    "limit": self.page_size,
                },
                "jobFilter": {
                    "completedJobLookupTime": lookup_seconds,
                    "showAgedJobs": True,
                    "hideAdminJobs": False,
                    "clientList": [],
                    "jobTypeList": [],
                },
            }
            body = _json(self._api.post("/Jobs", json=payload))
            entries = body.get("jobs") or []
            jobs = [job_in_from_summary(e["jobSummary"]) for e in entries if "jobSummary" in e]
            logger.info(
                "Jobs page offset={} returned {} of {} total",
                offset,
                len(entries),
                body.get("totalRecordsWithoutPaging"),
            )
            if jobs:
                yield jobs
            if len(entries) < self.page_size:
                return
            offset += self.page_size

    def get_job(self, job_id: int) -> JobIn | None:
        """One job's current summary, or None if the CommCell no longer
        knows the job at all."""
        try:
            body = _json(self._api.get(f"/Job/{job_id}"))
        except NotFoundError:
            return None
        for entry in body.get("jobs") or []:
            if "jobSummary" in entry:
                return job_in_from_summary(entry["jobSummary"])
        return None
