"""Playwright session management for Kroger (2026).

Kroger's edge (Akamai) resets the HTTP/2 stream for headless/automated
fingerprints, so this tool drives a *headed* Chromium window against a
persistent profile. The user signs in once; the SSO cookies persist in the
profile dir, so every later run is password-free (the window still opens, but
no credentials are needed).

Selector notes (verified against kroger.com in 2026):
  sign-in email field   #signInName   (aria "Email Address")
  sign-in password      #password     (aria "Password")
  sign-in submit        button#continue ("Sign In")
  cookie banner (OT)    #ot-sdk-btn / [id^=close-pc-btn]
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager

from playwright.sync_api import BrowserContext, Page, sync_playwright

from .config import BROWSER_PROFILE_DIR, ensure_state_dir

USER_AGENT_FALLBACK = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class SessionError(RuntimeError):
    """Raised when the persisted Kroger session is missing or expired."""


def _channel() -> str | None:
    """Optional real-browser channel. Default = Playwright's bundled Chromium,
    which is proven to pass Kroger's WAF in headed mode. Set
    KROGER_BROWSER_CHANNEL=chrome to drive the user's installed Chrome."""
    ch = os.environ.get("KROGER_BROWSER_CHANNEL", "").strip()
    return ch or None


def _launch_kwargs(headless: bool) -> dict:
    return {
        "user_data_dir": str(BROWSER_PROFILE_DIR),
        "channel": _channel(),
        "headless": headless,
        "viewport": {"width": 1280, "height": 900},
        "user_agent": USER_AGENT_FALLBACK,
        "locale": "en-US",
        "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    }


def _is_signed_in(page: Page, domain: str) -> bool:
    """Signed-in => the profile API returns JSON in a <pre>. Signed-out => a
    404 error page (no <pre>)."""
    try:
        page.goto(
            f"https://www.{domain}/accountmanagement/api/profile",
            wait_until="domcontentloaded",
            timeout=45_000,
        )
    except Exception:
        return False
    try:
        body = page.locator("pre").first
        body.wait_for(timeout=10_000)
        return body.inner_text().strip().startswith("{")
    except Exception:
        return False


def _dismiss_cookie_banner(page: Page) -> None:
    """Kroger loads a OneTrust consent banner; accept/close it if present."""
    for sel in (
        "#ot-sdk-btn",
        "button#close-pc-btn-handler",
        "#close-pc-btn-handler",
        "button:has-text('Continue')",
        "button:has-text('Confirm My Choices')",
        "button:has-text('Accept All')",
    ):
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1500):
                loc.click(timeout=1500)
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


@contextmanager
def browser_session(domain: str = "kroger.com", headless: bool = False, require_session: bool = True):
    """Yield a Page bound to the persistent Kroger session.

    Kroger requires a headed browser, so `headless` defaults to False. If
    `require_session` is True and the session is gone, raises SessionError.
    """
    ensure_state_dir()
    with sync_playwright() as pw:
        context: BrowserContext = pw.chromium.launch_persistent_context(**_launch_kwargs(headless))
        try:
            page = context.pages[0] if context.pages else context.new_page()
            _dismiss_cookie_banner(page)
            if require_session and not _is_signed_in(page, domain):
                context.close()
                raise SessionError("No active Kroger session. Run: kroger-aio login")
            yield page
        finally:
            context.close()


def run_visible_login(domain: str, username: str, password: str, console=None) -> bool:
    """Open a visible Chromium window and log in. Returns True on success."""
    ensure_state_dir()
    from rich.console import Console

    out = console or Console()
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(**_launch_kwargs(headless=False))
        try:
            page = context.pages[0] if context.pages else context.new_page()
            out.print(
                f"[bold]A Chromium window is opening on www.{domain}.[/bold] "
                "It signs in automatically; if it shows a CAPTCHA or 2FA, "
                "solve it in the window. It closes itself when done."
            )
            page.goto(
                f"https://www.{domain}/signin?redirectUrl=/cl/coupons",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            _dismiss_cookie_banner(page)

            # Already signed in from a prior run?
            if _is_signed_in(page, domain):
                out.print("[green]Session already active — nothing to do.[/green]")
                return True

            # Fill the 2026 sign-in form.
            try:
                email = page.locator("#signInName")
                email.wait_for(timeout=25_000)
                email.click()
                email.type(username, delay=15)
                pwd = page.locator("#password")
                pwd.click()
                pwd.type(password, delay=15)
                page.locator("button#continue").first.click()
            except Exception:
                out.print(
                    "[yellow]The sign-in form changed — please finish the login manually "
                    "in the open window.[/yellow]"
                )

            # Wait for the coupons page (signed-in destination).
            for _ in range(24):
                try:
                    page.wait_for_url("**/cl/coupons**", timeout=5_000)
                    break
                except Exception:
                    time.sleep(1)
            ok = _is_signed_in(page, domain)
            if ok:
                out.print("[bold green]Signed in. Session saved for future runs.[/bold green]")
            else:
                out.print(
                    "[bold red]Sign-in did not complete (CAPTCHA/2FA/incorrect "
                    "credentials?). Re-run: kroger-aio login[/bold red]"
                )
            return ok
        finally:
            context.close()
