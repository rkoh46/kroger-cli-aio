"""Account data: profile, points balance, purchases summary."""

from __future__ import annotations

import json
from collections import defaultdict

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeoutError

from .config import AccountProfile


def _api_json(page: Page, url: str):
    """Navigate to a Kroger API endpoint that returns JSON in a <pre> tag."""
    page.goto(url, wait_until="domcontentloaded")
    try:
        pre = page.locator("pre").first
        pre.wait_for(timeout=20_000)
        return json.loads(pre.inner_text())
    except (PWTimeoutError, json.JSONDecodeError):
        # Fallback: some endpoints return bare JSON.
        try:
            body = page.locator("body").inner_text(timeout=5000)
            return json.loads(body.strip())
        except Exception:
            return None


def get_profile(page: Page, domain: str) -> dict | None:
    data = _api_json(page, f"https://www.{domain}/accountmanagement/api/profile")
    if isinstance(data, dict):
        profile = AccountProfile.load()
        profile.data = _flatten_profile(data)
        profile.save()
    return data


def _flatten_profile(raw: dict | None) -> dict:
    raw = raw or {}
    addr = raw.get("address") or {}
    return {
        "first_name": raw.get("firstName", ""),
        "last_name": raw.get("last_name", ""),
        "email": raw.get("emailAddress", ""),
        "loyalty_card": raw.get("loyaltyCardNumber", ""),
        "phone": raw.get("mobilePhoneNumber", ""),
        "address_line1": addr.get("addressLine1", ""),
        "city": addr.get("city", ""),
        "state": addr.get("stateCode", ""),
        "zip": addr.get("zip", ""),
    }


def get_points(page: Page, domain: str) -> list | None:
    return _api_json(page, f"https://www.{domain}/accountmanagement/api/points-summary")


def get_purchases(page: Page, domain: str) -> list | None:
    data = _api_json(page, f"https://www.{domain}/mypurchases/api/v1/receipt/summary/by-user-id")
    return data if isinstance(data, list) else None


def summarize_purchases(purchases: list) -> dict:
    """Group purchases by year: store visits, dollars spent, dollars saved."""
    years: dict[int, dict] = defaultdict(lambda: {"store_visits": 0, "total": 0.0, "total_savings": 0.0})
    total = {"store_visits": 0, "total": 0.0, "total_savings": 0.0}
    first = last = None

    for p in purchases:
        first = first or p
        last = p
        year = int(p.get("transactionTime", "0000")[:4])
        if year and year >= 2000:
            if "total" in p:
                years[year]["total"] += float(p["total"])
                years[year]["store_visits"] += 1
                total["total"] += float(p["total"])
                total["store_visits"] += 1
            if "totalSavings" in p:
                years[year]["total_savings"] += float(p["totalSavings"])
                total["total_savings"] += float(p["totalSavings"])

    if last is None:
        return {}
    return {
        "years": dict(sorted(years.items())),
        "total": total,
        "first_purchase": first.get("transactionTime", ""),
        "last_purchase": last.get("transactionTime", ""),
    }
