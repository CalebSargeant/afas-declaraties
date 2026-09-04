#!/usr/bin/env python3
"""Establish (or refresh) a persistent authenticated AFAS InSite session.

Run this once interactively to seed the browser profile, then again on a timer
to keep it warm. Every run that finds a live session is a silent redirect and
enters no credentials at all.

    INSITE_HOST=<host> OP_ITEM_NAME=<item> python3 scripts/bootstrap_session.py --headed

The profile it writes is a replayable, MFA-satisfying corporate session.
Treat it as a credential: it is gitignored, and it must never be uploaded,
attached to an issue, or copied out of the machine it was created on.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from playwright.sync_api import sync_playwright  # noqa: E402

from afas_declaraties import onepassword  # noqa: E402
from afas_declaraties.entra import (  # noqa: E402
    Credentials,
    EntraLoginError,
    make_settled_predicate,
    on_microsoft_login,
    sign_in,
)

logger = logging.getLogger("bootstrap")

# InSite routes that appear mid-handshake and must not count as "arrived".
TRANSIENT_PATHS = ("/signin-oidc", "/signin", "/login", "/authenticationhandler")

DEFAULT_PROFILE = REPO_ROOT / "browser-profile"


def wait_until_settled(page, settled, *, timeout_ms: int) -> None:
    try:
        page.wait_for_function(
            """([host, transient]) => {
                if (location.hostname !== host && !location.hostname.endsWith('.' + host)) return false;
                const path = location.pathname.toLowerCase();
                return !transient.some(m => path.includes(m));
            }""",
            arg=[settled.app_host, list(TRANSIENT_PATHS)],
            timeout=timeout_ms,
        )
    except Exception as exc:
        raise EntraLoginError(f"never settled on {settled.app_host}; stuck at {page.url}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument(
        "--manual",
        action="store_true",
        help=(
            "open a visible browser and wait for you to sign in by hand, instead of "
            "reading credentials from 1Password. Implies --headed. Use for first-run "
            "setup, or whenever the automated path is blocked."
        ),
    )
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--timeout", type=int, default=60, help="settle timeout, seconds")
    parser.add_argument("--trace", type=Path, help="write a Playwright trace here")
    args = parser.parse_args()

    if args.manual:
        args.headed = True
        args.timeout = max(args.timeout, 300)

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    host = os.environ.get("INSITE_HOST")
    if not host:
        logger.error("INSITE_HOST is required (the AFAS InSite hostname, no scheme)")
        return 2
    item = os.environ.get("OP_ITEM_NAME", "Microsoft")
    vault = os.environ.get("OP_VAULT", "Private")

    args.profile.mkdir(parents=True, exist_ok=True)
    settled = make_settled_predicate(host, TRANSIENT_PATHS)
    settled.app_host = host  # noqa: B010 - carried for the in-page predicate

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(args.profile),
            headless=not args.headed,
            channel="chromium",
            viewport={"width": 1440, "height": 900},
            locale="nl-NL",
            timezone_id="Europe/Amsterdam",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        if args.trace:
            context.tracing.start(screenshots=True, snapshots=True, sources=True)

        page = context.pages[0] if context.pages else context.new_page()
        try:
            logger.info("navigating to the portal")
            page.goto(f"https://{host}/", wait_until="domcontentloaded")

            if not on_microsoft_login(page):
                logger.info("existing session accepted -- no credentials entered")
            elif args.manual:
                logger.info("=" * 68)
                logger.info("Sign in by hand in the browser window that just opened.")
                logger.info("Tick 'Stay signed in?' -- without it the saved session")
                logger.info("lasts hours instead of weeks.")
                logger.info("Waiting up to %ds. Nothing is typed for you.", args.timeout)
                logger.info("=" * 68)
            else:
                logger.info("no live session -- performing a full sign-in")
                creds = Credentials(
                    username=onepassword.get_field(item, "username", vault),
                    password=onepassword.get_field(item, "password", vault),
                    totp=lambda: onepassword.get_totp(item, vault),
                )
                sign_in(page, creds)

            wait_until_settled(page, settled, timeout_ms=args.timeout * 1000)
            logger.info("session is live at %s", page.url)
            logger.info("profile persisted to %s", args.profile)
            return 0
        except EntraLoginError as exc:
            logger.error("sign-in failed: %s", exc)
            return 1
        finally:
            if args.trace:
                context.tracing.stop(path=str(args.trace))
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
