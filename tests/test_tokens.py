"""Access-token renewal: when it happens, and that the new pair reaches
the .env file intact."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

from commvault_sdk import TokenSet

from blt.collector.settings import load_settings
from blt.collector.tokens import env_token_saver, update_env_file

BASE = "https://cs.test/commandcenter/api"
RENEWED = {
    "accessToken": "new-access",
    "refreshToken": "new-refresh",
    "tokenExpiryTimestamp": 1_800_000_000,
    "renewableUntilTimestamp": 1_805_000_000,
}


def test_renewed_tokens_are_written_back_to_the_env_file(tmp_path: Path) -> None:
    env = tmp_path / "prod.env"
    env.write_text(
        "# my commcell\n"
        "CV_BASE_URL=https://cs.test/api\n"
        "CV_ACCESS_TOKEN=old-access\n"
        "# CV_REFRESH_TOKEN=commented-out\n"
        "CV_REFRESH_TOKEN=old-refresh\n"
        "CV_PAGE_SIZE=250\n"
    )
    (tmp_path / "blt.env").write_text("BLT_API_KEY=k\n")
    new = TokenSet(
        "new-access",
        "new-refresh",
        datetime(2026, 10, 6, 3, 0, tzinfo=UTC),
        datetime(2026, 12, 4, 7, 59, 59, tzinfo=UTC),
    )

    env_token_saver(env)(new, RENEWED)

    assert env.read_text() == (
        "# my commcell\n"
        "CV_BASE_URL=https://cs.test/api\n"
        "CV_ACCESS_TOKEN=new-access\n"
        "# CV_REFRESH_TOKEN=commented-out\n"
        "CV_REFRESH_TOKEN=new-refresh\n"
        "CV_PAGE_SIZE=250\n"
        "CV_TOKEN_EXPIRES_AT=2026-10-06T03:00:00Z\n"
        "CV_TOKEN_RENEWABLE_UNTIL=2026-12-04T07:59:59Z\n"
    )
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    # The CommServe's answer is kept verbatim as a safety net.
    assert json.loads((tmp_path / ".prod.renewed.json").read_text()) == RENEWED

    # And the next run reads back exactly what was saved.
    settings = load_settings("prod", tmp_path)
    assert settings.cv_access_token.get_secret_value() == "new-access"  # type: ignore[union-attr]
    assert settings.cv_refresh_token.get_secret_value() == "new-refresh"  # type: ignore[union-attr]
    assert settings.cv_token_expires_at == new.expires_at
    assert settings.cv_token_renewable_until == new.renewable_until


def test_blank_values_in_the_env_file_mean_unset(tmp_path: Path) -> None:
    env = tmp_path / "prod.env"
    env.write_text("CV_BASE_URL=https://cs.test/api\nCV_ACCESS_TOKEN=t\n")
    (tmp_path / "blt.env").write_text("BLT_API_KEY=k\n")
    update_env_file(env, {"CV_TOKEN_EXPIRES_AT": "", "CV_USERNAME": ""})

    settings = load_settings("prod", tmp_path)
    assert settings.cv_token_expires_at is None
    assert settings.cv_username is None
