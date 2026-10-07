"""Coupon operations: clip everything, check status, report deltas.

Selectors are centralized in COUPON_SELECTORS at the top so they can be
re-tuned against the live site without touching the logic. The `discover`
command dumps the live coupons page for inspection when Kroger changes markup.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeoutError

from .config import LAST_RUN_PATH, Ledger, ensure_state_dir

# ---------------------------------------------------------------------------
# Tunable selectors. `card` should match one container per coupon on the
# digital-coupons page; `clip_button` matches the un-clipped "Clip" button
# inside a card; `clipped_marker` matches the "Clipped" state of the button.
# These are starting points from the 2020 codebase (kds-Button--favorable)
# and MUST be verified against the live site via `kroger-aio discover`.
# ---------------------------------------------------------------------------
COUPON_SELECTORS: dict[str, str] = {
    "card": ".kds-CouponPromo, [class*='coupon-item'], [class*='CouponItem']",
    "clip_button": (
        "button.kds-Button--favorable:not([class*='unfavorable']), "
        "button:has-text('Clip')"
    ),
    "clipped_marker": "button:has-text('Clipped'), [class*='clipped']",
    "list_container": "main, #coupon-content, body",
}


def _ts() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _safe_json_loads(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _coupon_id_from_card(card) -> str:
    """Derive a stable id for a coupon card.

    Preference order: data attributes that Kroger exposes, else the product
    name + price. A stable id is what makes clip-all idempotent.
    """
    for attr in ("data-coupon-id", "data-id", "id"):
        value = card.get_attribute(attr)
        if value:
            return value
    name = card.locator("h3, h2, [class*='name'], [class*='title']").first
    price = card.locator("[class*='price'], [class*='Price']").first
    try:
        name_text = name.inner_text(timeout=1000).strip()
    except Exception:
        name_text = ""
    try:
        price_text = price.inner_text(timeout=1000).strip()
    except Exception:
        price_text = ""
    if name_text:
        # Cheap stable hash-free key: normalized name.
        import re

        norm = re.sub(r"\s+", " ", name_text.lower())
        return f"name:{norm}|{price_text}"
    return f"idx:{card.evaluate('el => Array.from(document.querySelectorAll(\"[data-coupon-id], .kds-CouponPromo\")).indexOf(el)')}"


def list_coupons(page: Page, domain: str, max_cards: int = 500) -> list[dict]:
    """Return the coupons currently on the digital-coupons page."""
    page.goto(f"https://www.{domain}/cl/coupons", wait_until="domcontentloaded")
    try:
        page.locator(COUPON_SELECTORS["list_container"]).wait_for(timeout=30_000)
    except PWTimeoutError:
        pass
    # Let lazy-loaded cards render.
    page.wait_for_timeout(2500)

    cards = page.locator(COUPON_SELECTORS["card"])
    count = min(cards.count(), max_cards)
    coupons: list[dict] = []
    for i in range(count):
        card = cards.nth(i)
        try:
            cid = _coupon_id_from_card(card)
            clipped = False
            try:
                clipped = card.locator(COUPON_SELECTORS["clipped_marker"]).count() > 0
            except Exception:
                pass
            name = ""
            try:
                name = (
                    card.locator("h3, h2, [class*='name'], [class*='title']").first
                    .inner_text(timeout=1000)
                    .strip()
                )
            except Exception:
                pass
            price = ""
            try:
                price = (
                    card.locator("[class*='price'], [class*='Price']").first
                    .inner_text(timeout=1000)
                    .strip()
                )
            except Exception:
                pass
            coupons.append({"id": cid, "name": name, "price": price, "clipped": clipped})
        except Exception:
            continue
    return coupons


def clip_all(page: Page, domain: str) -> dict:
    """Clip every coupon that is available and not yet clipped.

    Returns a summary dict: {clipped: n, skipped: n, failed: n, new: [names]}.
    The ledger is updated with every successfully clipped coupon.
    """
    ensure_state_dir()
    ledger = Ledger.load()
    prev_ids = set(ledger.clipped)

    page.goto(f"https://www.{domain}/cl/coupons", wait_until="domcontentloaded")
    try:
        page.locator(COUPON_SELECTORS["list_container"]).wait_for(timeout=30_000)
    except PWTimeoutError:
        pass
    page.wait_for_timeout(2500)
    # Dismiss any modal/overlay that blocks clicks.
    page.keyboard.press("Escape")

    result = {"clipped": 0, "skipped": 0, "failed": 0, "new": []}

    # Retry passes: each pass re-queries buttons because the DOM mutates as
    # coupons flip from "Clip" to "Clipped".
    for _pass in range(5):
        buttons = page.locator(COUPON_SELECTORS["clip_button"])
        n = buttons.count()
        if n == 0:
            break
        progress = 0
        for i in range(n):
            btn = buttons.nth(i)
            if not btn.is_visible():
                continue
            try:
                # Re-verify this button still exists & is clickable (DOM churn).
                btn.wait_for(timeout=1500)
                # Identify the parent card for ledger bookkeeping.
                card = btn.locator("xpath=ancestor::*[contains(@class,'oupon')][1]")
                if card.count() == 0:
                    card = btn.locator("xpath=..")
                cid = _coupon_id_from_card(card.first)
                name = ""
                try:
                    name = (
                        card.first.locator("h3, h2, [class*='name'], [class*='title']").first
                        .inner_text(timeout=1000)
                        .strip()
                    )
                except Exception:
                    pass
                if ledger.is_clipped(cid) and "Clipped" in (btn.inner_text(timeout=500) or ""):
                    result["skipped"] += 1
                    continue
                btn.scroll_into_view_if_needed()
                btn.click(timeout=3000)
                progress += 1
                ledger.mark(cid, name=name, when=_ts())
                if cid not in prev_ids:
                    result["new"].append(name or cid)
            except Exception:
                result["failed"] += 1
        result["clipped"] += progress
        if progress == 0:
            break
        # Give the page time to re-render before the next pass.
        page.wait_for_timeout(1500)
        # Scroll to bottom to trigger lazy-load of additional rows.
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1000)

    ledger.save()
    _write_last_run(domain, result)
    return result


def clip_status(page: Page, domain: str) -> dict:
    """Report how many coupons are clipped vs. still available (unclipped)."""
    coupons = list_coupons(page, domain)
    total = len(coupons)
    clipped = sum(1 for c in coupons if c["clipped"])
    available = total - clipped
    unclipped = [c for c in coupons if not c["clipped"]]
    return {
        "total": total,
        "clipped": clipped,
        "available_unclipped": available,
        "unclipped_sample": unclipped[:25],
        "clipped_count_in_ledger": len(Ledger.load().clipped),
    }


def _write_last_run(domain: str, result: dict) -> None:
    ensure_state_dir()
    LAST_RUN_PATH.write_text(
        json.dumps({"domain": domain, "at": _ts(), "result": result}, indent=2)
    )


def read_last_run() -> dict | None:
    if LAST_RUN_PATH.exists():
        try:
            return json.loads(LAST_RUN_PATH.read_text())
        except json.JSONDecodeError:
            return None
    return None


def discover(page: Page, domain: str, out_path: Path) -> None:
    """Save the live coupons-page HTML + a list of button candidates.

    Used once after login to retune COUPON_SELECTORS for the current markup.
    """
    ensure_state_dir()
    page.goto(f"https://www.{domain}/cl/coupons", wait_until="domcontentloaded")
    try:
        page.locator("main, body").first.wait_for(timeout=30_000)
    except PWTimeoutError:
        pass
    page.wait_for_timeout(3000)

    html = page.content()
    out_path.write_text(html)

    buttons = page.locator("button")
    btn_info: list[dict] = []
    for i in range(min(buttons.count(), 400)):
        b = buttons.nth(i)
        try:
            text = (b.inner_text(timeout=500) or "").strip()
            cls = b.get_attribute("class") or ""
            if any(k in text.lower() for k in ("clip", "clipped")) or "favorable" in cls:
                btn_info.append({"text": text[:80], "class": cls[:160]})
        except Exception:
            continue

    meta = {
        "url": page.url,
        "title": page.title(),
        "clip_like_buttons": btn_info,
        "note": "Inspect the HTML file + this list, then update COUPON_SELECTORS in coupons.py",
    }
    out_path.with_suffix(".buttons.json").write_text(json.dumps(meta, indent=2))
