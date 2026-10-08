"""Playwright session management for Kroger (2026).

Kroger's edge (Akamai) resets the HTTP/2 stream for headless/automated
fingerprints, so this tool drives a *headed* Chromium window against a
persistent profile. The user signs in once; the SSO cookies persist in the
profile dir, so every later run is password-free (the window still opens, but
no credentials are needed).

Two hard-won 2026 findings baked in here:
  * Do NOT override the user agent. A mismatched UA string (vs. the real
    client hints) makes Akamai 403 the page's own API calls, so the coupons
    list never loads. Let Chromium report its true identity.
  * Brand domains (Ralphs, Dillons, King Soopers, ...) mount the app under a
    /savings/ path prefix; kroger.com does not.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager

from playwright.sync_api import BrowserContext, Page, sync_playwright

from .config import BROWSER_PROFILE_DIR, coupons_url, ensure_state_dir, path_prefix


class SessionError(RuntimeError):
    """Raised when the persisted Kroger session is missing or expired."""


def _channel() -> str | None:
    """Optional real-browser channel. Default = Playwright's bundled Chromium,
    which is proven to pass Kroger's WAF in headed mode. Set
    KROGER_BROWSER_CHANNEL=chrome to drive the user's installed Chrome."""
    ch = os.environ.get("KROGER_BROWSER_CHANNEL", "").strip()
    return ch or None


def _launch_kwargs(headless: bool) -> dict:
    # NOTE: no user_agent override on purpose (see module docstring).
    return {
        "user_data_dir": str(BROWSER_PROFILE_DIR),
        "channel": _channel(),
        "headless": headless,
        "viewport": {"width": 1280, "height": 900},
        "locale": "en-US",
        "args": ["--no-sandbox"],
    }


def _signed_in_state(page: Page) -> str:
    """Return 'signed_in', 'signed_out', or 'unknown' from the coupons page.

    Verified 2026: signed-in cards show buttons 'Clip'/'Unclip'; signed-out
    shows 'Sign In To Clip'.
    """
    try:
        states = page.evaluate(
            """() => Array.from(document.querySelectorAll('.CouponActionButton'))
                .map(b => (b.innerText || '').trim())"""
        )
    except Exception:
        return "unknown"
    for s in states:
        if s in ("Clip", "Unclip"):
            return "signed_in"
    if "Sign In To Clip" in states:
        return "signed_out"
    # No buttons rendered yet (still loading / filtered / WAF retry) — unknown.
    return "unknown"


def _open_coupons(page: Page, domain: str, timeout_ms: int = 60_000) -> None:
    """Navigate to the coupons page and wait for it to render, retrying
    through Akamai's intermittent API resets (403s that self-heal)."""
    url = coupons_url(domain)
    for _attempt in range(4):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:
            time.sleep(2)
            continue
        _dismiss_cookie_banner(page)
        deadline = time.time() + 40
        while time.time() < deadline:
            if _signed_in_state(page) != "unknown":
                return
            time.sleep(1)
        time.sleep(3)  # still unknown — retry the navigation


def _is_signed_in(page: Page, domain: str) -> bool:
    try:
        _open_coupons(page, domain)
        return _signed_in_state(page) == "signed_in"
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
        "button:has-text('Allow All')",
    ):
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1200):
                loc.click(timeout=1500)
                page.wait_for_timeout(400)
                return
        except Exception:
            continue


@contextmanager
def browser_session(domain: str = "kroger.com", require_session: bool = True):
    """Yield a Page bound to the persistent Kroger session, already on the
    coupons page. Kroger requires a headed browser. If `require_session` is
    True and the session is gone, raises SessionError.

    The app's own Atlas request headers (x-laf-object, x-facility-id, ...)
    are captured during this navigation and stashed on the page as
    ``_kroger_atlas_headers`` for the API-driven coupon engine."""
    ensure_state_dir()
    with sync_playwright() as pw:
        context: BrowserContext = pw.chromium.launch_persistent_context(
            **_launch_kwargs(headless=False)
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            atlas: dict = {}

            def _capture(r) -> None:
                if not atlas and "savings-coupons" in r.url and r.method == "GET":
                    atlas.update({
                        k: v for k, v in r.headers.items()
                        if k.lower().startswith("x-")
                    })

            page.on("request", _capture)
            _open_coupons(page, domain)
            if require_session and _signed_in_state(page) != "signed_in":
                context.close()
                raise SessionError("No active Kroger session. Run: kroger-aio login")
            page._kroger_atlas_headers = dict(atlas)  # type: ignore[attr-defined]
            yield page
        finally:
            context.close()


def run_visible_login(domain: str, username: str, console=None) -> bool:
    """Open a visible Chromium window and log in. Returns True on success.

    The password is typed by the USER into the browser's own masked field
    (the "secure prompt") — it never passes through this process, the shell,
    env, or disk. The email is auto-filled.
    """
    ensure_state_dir()
    from rich.console import Console

    out = console or Console()
    redirect = f"{path_prefix(domain)}/cl/coupons"
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(**_launch_kwargs(headless=False))
        try:
            page = context.pages[0] if context.pages else context.new_page()
            out.print(
                f"[bold]A Chromium window is opening on www.{domain}.[/bold]\n"
                "[bold]Type your Kroger password into the password field in that "
                "window[/bold] (the email is pre-filled). It submits automatically "
                "once the field has text. If a CAPTCHA or 2FA appears, solve it in "
                "the window. The window closes itself when done."
            )
            page.goto(
                f"https://www.{domain.lower()}/signin?redirectUrl={redirect}",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            _dismiss_cookie_banner(page)

            # Fill email, focus the password field for the user.
            try:
                email = page.locator("#signInName")
                email.wait_for(timeout=25_000)
                email.click()
                email.type(username, delay=15)
                page.locator("#password").first.click()
            except Exception:
                out.print(
                    "[yellow]The sign-in form changed — please fill both fields "
                    "and press Sign In manually in the open window.[/yellow]"
                )

            # Wait for the user to type their password, then submit.
            # Check only a boolean (length>0) so the value never enters us.
            out.print("[dim]Waiting for you to type the password in the window…[/dim]")
            filled = False
            for _ in range(180):
                time.sleep(1)
                try:
                    if "/cl/coupons" in page.url:
                        filled = True
                        break
                    filled = page.evaluate(
                        "() => { const p = document.querySelector('#password'); "
                        "return !!(p && p.value.length > 0); }"
                    )
                    if filled:
                        break
                except Exception:
                    if "/cl/coupons" in page.url:
                        filled = True
                        break
            if not filled:
                out.print("[bold red]No password entered in time. Re-run login.[/bold red]")
                return False
            if "/cl/coupons" not in page.url:
                try:
                    page.locator("button#continue").first.click(timeout=3000)
                except Exception:
                    try:
                        page.keyboard.press("Enter")
                    except Exception:
                        pass

            # Wait for the signed-in coupons page.
            ok = False
            for _ in range(90):
                time.sleep(1)
                if _signed_in_state(page) == "signed_in":
                    ok = True
                    break
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
