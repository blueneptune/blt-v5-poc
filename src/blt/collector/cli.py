"""`blt-collect` - what collect.sh runs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger
from sdk_primer import APIClient, SDKError, configure_logging

from blt.commvault.auth import TokenSet
from blt.commvault.client import CommvaultClient

from .collect import run_collection
from .settings import commcell_env_path, load_settings
from .store import BltStore
from .tokens import env_token_saver, warn_if_regeneration_due


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="blt-collect", description="Collect Commvault job history into the blt API."
    )
    parser.add_argument("--commcell", required=True, help="names config/<commcell>.env")
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument(
        "--full", action="store_true", help="ignore the watermark and re-collect everything"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="only verify the CommCell credentials work; collect nothing",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    configure_logging(level=args.log_level.upper())
    try:
        settings = load_settings(args.commcell, args.config_dir)
    except Exception as exc:  # missing file or missing/invalid setting
        logger.error("Configuration problem for {}: {}", args.commcell, exc)
        sys.exit(78)

    tokens = None
    if settings.cv_access_token:
        tokens = TokenSet(
            access_token=settings.cv_access_token.get_secret_value(),
            refresh_token=(
                settings.cv_refresh_token.get_secret_value() if settings.cv_refresh_token else None
            ),
            expires_at=settings.cv_token_expires_at,
            renewable_until=settings.cv_token_renewable_until,
        )
        warn_if_regeneration_due(args.commcell, settings.cv_token_renewable_until)

    commvault = CommvaultClient(
        base_url=settings.cv_base_url,
        username=settings.cv_username,
        password=settings.cv_password.get_secret_value() if settings.cv_password else None,
        access_token=tokens,
        on_token_renew=env_token_saver(commcell_env_path(args.commcell, args.config_dir)),
        verify_tls=settings.cv_verify_tls,
        ca_bundle=settings.cv_ca_bundle,
        page_size=settings.cv_page_size,
        timeout=settings.cv_timeout_seconds,
    )

    if args.check:
        try:
            with commvault:
                info = commvault.commserve_info()
        except SDKError as exc:
            logger.error("Could not authenticate to {}: {}", args.commcell, exc)
            sys.exit(1)
        logger.info(
            "Authenticated to {}: CommServe {} version {}",
            args.commcell,
            info.get("hostName"),
            info.get("csVersionInfo"),
        )
        return

    try:
        with (
            commvault,
            APIClient(
                base_url=settings.blt_api_url,
                default_headers={"X-API-Key": settings.blt_api_key.get_secret_value()},
                timeout=settings.cv_timeout_seconds,
            ) as api,
        ):
            run_collection(
                commvault,
                BltStore(api, args.commcell),
                initial_lookback_days=settings.cv_initial_lookback_days,
                overlap_minutes=settings.cv_overlap_minutes,
                force_full=args.full,
            )
    except SDKError as exc:
        logger.error("Collection for {} failed: {}", args.commcell, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
