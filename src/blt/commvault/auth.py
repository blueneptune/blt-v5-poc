"""Commvault access tokens, with renewal.

An access token is short-lived (two hours on the eval CommServe) and
comes with a refresh token. POST /V4/AccessToken/Renew trades the pair
for a *new* pair - the old ones stop working - until the token's
"renewable until" date, after which someone has to create a fresh token
in Command Center.

Because renewal replaces both tokens, whoever holds them has to save the
new pair somewhere durable the moment it arrives, or the next run starts
with a dead token. That is `on_renew` here; the collector points it at
the CommCell's .env file (see blt.collector.tokens).
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from loguru import logger
from sdk_primer import AuthenticationError

_RENEW_PATH = "/V4/AccessToken/Renew"


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    renewable_until: datetime | None = None


def _from_epoch(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC) if value else None
    except (TypeError, ValueError):
        return None


def _now() -> datetime:
    return datetime.now(UTC)


class AccessTokenAuth(httpx.Auth):
    """Sends `<header>: Bearer <access token>` on every request - the
    header is `Authtoken` on a customer-managed CommServe and
    `Authorization` on Commvault Cloud (SaaS) - renewing
    first when the token is known to be about to expire, and renewing-
    then-retrying once when a request comes back 401 anyway (expiry not
    known, or the two machines' clocks disagree).

    Without a refresh token it only attaches the header: a 401 is then
    simply an authentication failure.
    """

    def __init__(
        self,
        base_url: str,
        tokens: TokenSet,
        *,
        client: httpx.Client,
        on_renew: Callable[[TokenSet, dict[str, Any]], None] | None = None,
        header: str = "Authtoken",
        reload: Callable[[], TokenSet | None] | None = None,
        renew_lock: Callable[[], AbstractContextManager[object]] = nullcontext,
        renew_margin: timedelta = timedelta(minutes=5),
        now: Callable[[], datetime] = _now,
    ) -> None:
        self._renew_url = base_url.rstrip("/") + _RENEW_PATH
        self.tokens = tokens
        self._client = client
        self._on_renew = on_renew
        self._header = header
        # For a token shared by several runs (an MSP's one token used for
        # each of its tenants): `reload` reads the pair currently on
        # disk, and `renew_lock` makes renewals take turns. Without them
        # two runs would each trade in the same old pair, and the second
        # would be refused - its pair having just been replaced.
        self._reload = reload
        self._renew_lock = renew_lock
        self._renew_margin = renew_margin
        self._now = now

    def _expiring(self) -> bool:
        expires_at = self.tokens.expires_at
        return expires_at is not None and self._now() >= expires_at - self._renew_margin

    def renew(self) -> TokenSet:
        with self._renew_lock():
            if self._reload is not None:
                latest = self._reload()
                if latest is not None and latest.access_token != self.tokens.access_token:
                    # Another run renewed it while this one was working.
                    logger.info("Picked up a Commvault token renewed by another run")
                    self.tokens = latest
                    return latest
            return self._renew()

    def _renew(self) -> TokenSet:
        old = self.tokens
        if not old.refresh_token:
            raise AuthenticationError(
                "The Commvault access token has expired and there is no refresh token to "
                "renew it with - create a new token in Command Center."
            )
        logger.info("Renewing the Commvault access token")
        payload = {"accessToken": old.access_token, "refreshToken": old.refresh_token}
        response = self._client.post(
            self._renew_url, json=payload, headers={self._header: f"Bearer {old.access_token}"}
        )
        if response.status_code == 401:
            # The access token being presented is the thing that expired;
            # the pair in the body is what actually authorises a renewal.
            response = self._client.post(self._renew_url, json=payload)

        try:
            body: dict[str, Any] = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or body.get("errorCode") or not body.get("accessToken"):
            reason = body.get("errorMessage") or f"HTTP {response.status_code}"
            hint = ""
            if old.renewable_until is not None and self._now() > old.renewable_until:
                hint = f" (it was only renewable until {old.renewable_until:%Y-%m-%d})"
            raise AuthenticationError(
                f"Commvault refused to renew the access token: {reason}{hint}. "
                "Create a new token in Command Center."
            )

        self.tokens = TokenSet(
            access_token=str(body["accessToken"]),
            refresh_token=str(body.get("refreshToken") or old.refresh_token),
            expires_at=_from_epoch(body.get("tokenExpiryTimestamp")),
            renewable_until=_from_epoch(body.get("renewableUntilTimestamp")) or old.renewable_until,
        )
        if self._on_renew is not None:
            self._on_renew(self.tokens, body)
        return self.tokens

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        renewed = False
        if self.tokens.refresh_token and self._expiring():
            self.renew()
            renewed = True
        request.headers[self._header] = f"Bearer {self.tokens.access_token}"
        response = yield request

        if response.status_code == 401 and self.tokens.refresh_token and not renewed:
            logger.warning("Commvault answered 401, renewing the access token and retrying once")
            self.renew()
            request.headers[self._header] = f"Bearer {self.tokens.access_token}"
            yield request
