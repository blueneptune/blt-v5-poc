"""Writing renewed Commvault tokens back to a CommCell's .env file.

Renewal invalidates the old access/refresh pair, so the new one has to
reach disk before anything else can go wrong. Two things are written, in
this order:

1. config/.<commcell>.renewed.json - the CommServe's renewal response,
   verbatim. A safety net: if the response ever has a shape this code
   doesn't expect, the new tokens are still recoverable by hand.
2. config/<commcell>.env - the CV_ACCESS_TOKEN / CV_REFRESH_TOKEN lines
   replaced in place, plus CV_TOKEN_EXPIRES_AT and
   CV_TOKEN_RENEWABLE_UNTIL (the date a brand-new token has to be created
   by). Every other line, comments included, is left as it was.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from blt.commvault.auth import TokenSet

# How long before the renewable-until date runs start warning about it.
REGENERATE_WARNING = timedelta(days=7)


def _write_private(path: Path, text: str) -> None:
    """Atomic replace, owner-only: a reader sees the old file or the new
    one, never half of each, and the tokens are never world-readable."""
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.replace(tmp, path)


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    """Set KEY=value lines in an env file, replacing a key where it
    already appears (commented-out lines don't count) and appending the
    ones that don't."""
    remaining = dict(updates)
    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in remaining:
            lines[index] = f"{match.group(1)}={remaining.pop(match.group(1))}"
    lines.extend(f"{key}={value}" for key, value in remaining.items())
    _write_private(path, "\n".join(lines) + "\n")


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def env_token_saver(env_path: Path) -> Callable[[TokenSet, dict[str, Any]], None]:
    """An AccessTokenAuth `on_renew` that persists to `env_path`."""
    raw_path = env_path.with_name(f".{env_path.stem}.renewed.json")

    def save(tokens: TokenSet, raw_response: dict[str, Any]) -> None:
        _write_private(raw_path, json.dumps(raw_response, indent=2) + "\n")
        updates = {"CV_ACCESS_TOKEN": tokens.access_token}
        if tokens.refresh_token:
            updates["CV_REFRESH_TOKEN"] = tokens.refresh_token
        # Blank rather than stale when the CommServe didn't say: an
        # unknown expiry falls back to renewing on a 401, where a wrong
        # one would renew on every run.
        updates["CV_TOKEN_EXPIRES_AT"] = _iso(tokens.expires_at) if tokens.expires_at else ""
        if tokens.renewable_until:
            updates["CV_TOKEN_RENEWABLE_UNTIL"] = _iso(tokens.renewable_until)
        update_env_file(env_path, updates)
        logger.info(
            "Saved the renewed Commvault token to {} (expires {}, renewable until {})",
            env_path,
            tokens.expires_at or "unknown",
            tokens.renewable_until or "unknown",
        )

    return save


def warn_if_regeneration_due(commcell: str, renewable_until: datetime | None) -> None:
    if renewable_until is None:
        return
    remaining = renewable_until - datetime.now(UTC)
    if remaining <= timedelta(0):
        logger.error(
            "The access token for {} stopped being renewable on {:%Y-%m-%d} - create a new "
            "one in Command Center and put it in config/{}.env.",
            commcell,
            renewable_until,
            commcell,
        )
    elif remaining <= REGENERATE_WARNING:
        logger.warning(
            "The access token for {} can only be renewed until {:%Y-%m-%d} ({} days left) - "
            "create a new one in Command Center before then.",
            commcell,
            renewable_until,
            remaining.days,
        )
