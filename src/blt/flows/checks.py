"""Checking a CommCell or a Rubrik account from a worker, for
Administration -> Sources (NetBackup's check is in blt.flows.netbackup).

Each signs in with what the source's block holds and tries each read blt
makes, logging one OK or FAIL line per read. A run fails only when it
cannot sign in at all: a read the login may not make is a FAIL line, so
the log says what to give its role, not just that something is wrong.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from loguru import logger
from prefect import flow
from rubrik_sdk import RubrikClient
from sdk_primer import AuthenticationError, NotFoundError, SDKError

from blt.collector.cli import describe_login
from blt.collector.connect import build_client
from blt.netbackup.check import Check
from blt.storage.commvault import commvault_storage

from .blocks import BlockTokenStore, settings_from_blocks
from .flows import logs_to_prefect
from .rubrik import RubrikAccountBlock


def why(error: Exception, refused: str) -> str:
    """A failed read, said so that it points at the fix."""
    if isinstance(error, AuthenticationError):
        return refused
    if isinstance(error, SDKError) and error.status_code == 403:
        return "this login may not read it (403) - give its role read access"
    if isinstance(error, NotFoundError):
        return "this version does not have it (404)"
    return str(error) or type(error).__name__


def attempt(name: str, call: Callable[[], str], refused: str) -> Check:
    try:
        return Check(name, True, call())
    except Exception as error:  # noqa: BLE001 - each check reports, never stops the rest
        return Check(name, False, why(error, refused))


def report(source: str, checks: list[Check]) -> str:
    """Logs each line; raises when the first - signing in - failed."""
    for check in checks:
        (logger.info if check.ok else logger.warning)(check.line())
    if not checks[0].ok:
        raise RuntimeError(f"Could not sign in to {source}: {checks[0].detail}")
    summary = f"{sum(c.ok for c in checks)} of {len(checks)} checks passed"
    logger.info(summary)
    return summary


COMMVAULT_REFUSED = (
    "the credentials were refused (401) - is the password right, or the token still valid? "
    "A token pair used anywhere else after blt was given it may have been renewed there."
)


@flow(name="blt-commvault-check", flow_run_name="check-{commcell}")
def commvault_check(commcell: str) -> str:
    """Signs in to the CommCell (or Commvault Cloud tenant) with its
    block's credentials and tries the reads blt makes: jobs and storage.
    A token that is due is renewed and saved back to the block, as a
    collection would."""
    with logs_to_prefect():
        settings, token_block = settings_from_blocks(commcell)
        saas = settings.cv_deployment == "saas"
        logger.info(
            "Checking {} {} at {}",
            "Commvault Cloud tenant" if saas else "CommCell",
            commcell,
            settings.cv_base_url,
        )
        commvault = build_client(settings, commcell, BlockTokenStore(token_block))
        with commvault:

            def jobs() -> str:
                total, _ = commvault.jobs.sample(86_400, limit=1)
                return f"readable ({total} in the last 24 hours)"

            def storage() -> str:
                problems: list[str] = []
                targets = commvault_storage(commvault, problems)
                note = f"; {len(problems)} partly unreadable" if problems else ""
                return f"{len(targets)} storage targets{note}"

            first = attempt(
                "sign in", lambda: describe_login(commvault, saas=saas), COMMVAULT_REFUSED
            )
            checks = [first]
            if first.ok:
                checks += [
                    attempt("jobs", jobs, COMMVAULT_REFUSED),
                    attempt("storage (pools, libraries)", storage, COMMVAULT_REFUSED),
                ]
        return report(commcell, checks)


RUBRIK_REFUSED = (
    "the service account was refused (401) - was its secret rotated in RSC, or the file from "
    "another account?"
)


@flow(name="blt-rubrik-check", flow_run_name="check-{account}")
def rubrik_check(account: str) -> str:
    """Signs in to the Rubrik Security Cloud account with its block's
    service account and tries the reads blt makes: jobs (activity
    series), inventory and SLA domains. Ends its RSC session after."""
    with logs_to_prefect():
        block = cast(RubrikAccountBlock, RubrikAccountBlock.load(account))
        service_account = block.service_account()
        logger.info(
            "Checking Rubrik account {} at {} as {}",
            account,
            service_account.base_url,
            block.service_account_name or "its service account",
        )
        with RubrikClient(service_account, verify_tls=block.verify_tls) as rsc:

            def sign_in() -> str:
                found = rsc.jobs.sample(1)
                return f"signed in; jobs readable ({'some' if found else 'none'} lately)"

            def inventory() -> str:
                found = rsc.inventory.sample(5)
                return f"readable ({len(found)} objects in a first look)"

            def sla_domains() -> str:
                page = rsc.sla_domains.list(first=50)
                return f"readable ({len(page.items)} SLA domains in a first page)"

            first = attempt("sign in and jobs", sign_in, RUBRIK_REFUSED)
            checks = [first]
            if first.ok:
                checks += [
                    attempt("inventory", inventory, RUBRIK_REFUSED),
                    attempt("SLA domains", sla_domains, RUBRIK_REFUSED),
                ]
        return report(account, checks)
