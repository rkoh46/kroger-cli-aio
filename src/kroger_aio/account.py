"""Account data: profile, points balance, purchases.

2026 Kroger/brand sites no longer expose the old
/accountmanagement/api/* JSON endpoints. Account data now lives in React
pages; we navigate to them and read the rendered text plus the embedded
__INITIAL_STATE__ where available.
"""

from __future__ import annotations

import re
import time

from playwright.sync_api import Page

# Verified on ralphs.com (2026). Account routes have NO /savings prefix;
# only the coupons app does.
ROUTE_ACCOUNT = "/account/dashboard/"
ROUTE_POINTS = "/account/pointssummary/"
ROUTE_PURCHASES = "/mypurchases"


def _goto(page: Page, domain: str, route: str) -> bool:
    url = f"https://www.{domain.lower()}{route}"
    for _ in range(3):
        try:
            r = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            if r is not None and r.status == 200:
                time.sleep(6)
                return True
        except Exception:
            time.sleep(2)
    return False


def _initial_state(page: Page) -> dict | None:
    try:
        return page.evaluate("() => window.__INITIAL_STATE__ || null")
    except Exception:
        return None


def get_profile(page: Page, domain: str) -> dict | None:
    """Best-effort account profile from the account dashboard page."""
    if not _goto(page, domain, ROUTE_ACCOUNT):
        return None
    state = _initial_state(page)
    body = page.evaluate("() => document.body.innerText")
    first = ""
    if state and isinstance(state.get("membership"), dict):
        mem = state["membership"]
        addr = mem.get("address") if isinstance(mem.get("address"), dict) else {}
        first = addr.get("firstName") or ""
    return {
        "first_name": first,
        "page_text": body[:4000],
        "has_state": state is not None,
    }


def get_points(page: Page, domain: str) -> dict | None:
    """Points/rewards balance scraped from the My Points page."""
    if not _goto(page, domain, ROUTE_POINTS):
        return None
    body = page.evaluate("() => document.body.innerText")
    figures = re.findall(r"([\d,]+)\s*(?:fuel\s+)?points?", body, re.IGNORECASE)
    dollars = re.findall(r"\$\s?([\d,]+(?:\.\d+)?)", body)
    return {
        "point_figures": figures[:12],
        "dollar_figures": dollars[:12],
        "page_text": body[:4000],
    }


def get_purchases(page: Page, domain: str) -> dict | None:
    """Purchases page scrape (the old by-user-id API is gone in 2026)."""
    if not _goto(page, domain, ROUTE_PURCHASES):
        return None
    body = page.evaluate("() => document.body.innerText")
    return {"page_text": body[:4000]}
