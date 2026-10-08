"""Coupon engine (2026, API-driven).

The 2026 Kroger/brand coupon app talks to an internal Atlas API that we
drive directly (verified live on ralphs.com):

  GET  /atlas/v1/savings-coupons/v1/coupons
       ?filter.status=unclipped&filter.status=active&page.size=N&page.offset=M
       -> data: [coupon objects], meta.coupons.userSavingsInfoByType.standard
          = {clippedCount, unclippedCount, clippedSavingsTotalValue, expiringCount}
       -> meta.coupons.filterSummaryByType.categories.options
          = the department filter ids (Beverages, Dairy, ...)

  POST /atlas/v1/savings-coupons/v1/clip-unclip
       body: application/x-www-form-urlencoded  action=CLIP|UNCLIP&couponId=<id>
       200 ok | 422 TooManyCouponsOnCard (card cap) | 422 CantUnloadCoupon
       | 400 INVALID_REQUEST

Direct server calls from plain Python (requests/curl) get their connections
reset by Akamai, but an in-page fetch() from the logged-in headed Chromium
passes, so the browser provides identity and the API does the work — no
button clicking, no virtualized-list cap.

Card limit: Kroger enforces a hard cap (~250 coupons) per loyalty card.
When the card is full the server rejects clips with 422 TooManyCouponsOnCard;
the engine stops cleanly and reports the remainder.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Page

from .config import LAST_RUN_PATH, Ledger, ensure_state_dir

BASE = "/atlas/v1/savings-coupons/v1"
UNCLIPPED = "filter.status=unclipped&filter.status=active"
ACTIVE = "filter.status=active"


def _ts() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _capture_headers(page: Page, domain: str) -> dict:
    """Atlas headers (x-laf-object, x-facility-id, ...). Uses the ones
    `browser_session` captured during navigation; falls back to a fresh
    navigation if the page was opened some other way."""
    stashed = getattr(page, "_kroger_atlas_headers", None)
    if stashed:
        return dict(stashed)
    from .config import coupons_url

    box: dict = {}

    def _on_request(r) -> None:
        if not box and "savings-coupons" in r.url and r.method == "GET":
            box.update({
                k: v for k, v in r.headers.items()
                if k.lower().startswith("x-")
            })

    page.on("request", _on_request)
    try:
        page.goto(coupons_url(domain), wait_until="domcontentloaded",
                  timeout=60_000)
    except Exception:
        pass
    deadline = time.time() + 45
    while not box and time.time() < deadline:
        time.sleep(1)
    page.remove_listener("request", _on_request)
    if not box:
        raise RuntimeError(
            "Could not capture the app's Atlas headers — the coupons page "
            "may have failed to load. Re-run."
        )
    return box


def _get(page: Page, h: dict, url: str, attempts: int = 3) -> dict:
    """In-page GET returning parsed JSON (or the raw error envelope)."""
    hdrs = {"Accept": "application/json, text/plain, */*"}.copy()
    hdrs.update(h)
    last: dict = {}
    for _ in range(attempts):
        try:
            res = page.evaluate(
                "async (u) => { const r = await fetch(u, {headers: "
                + json.dumps(hdrs)
                + "}); return {status: r.status, body: await r.text()}; }",
                url,
            )
            last = res
            if res["status"] == 200:
                return json.loads(res["body"])
        except Exception as e:
            last = {"status": 0, "body": str(e)}
        time.sleep(2)
    return last  # caller handles non-200


def _clip(page: Page, h: dict, action: str, coupon_id: str) -> tuple[int, str]:
    """POST clip-unclip. Returns (status, code-or-body)."""
    hdrs = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    hdrs.update(h)
    body = f"action={action}&couponId={coupon_id}"
    res = page.evaluate(
        "async () => { const r = await fetch("
        + json.dumps(f"{BASE}/clip-unclip")
        + ", {method: 'POST', headers: "
        + json.dumps(hdrs)
        + ", body: "
        + json.dumps(body)
        + "}); return {status: r.status, body: await r.text()}; }"
    )
    code = ""
    try:
        j = json.loads(res["body"])
        code = j.get("errors", {}).get("code", "")
    except Exception:
        code = res["body"][:120]
    return res["status"], code


def _savings(j: dict) -> dict:
    return j.get("meta", {}).get("coupons", {}) \
        .get("userSavingsInfoByType", {}).get("standard", {})


def _batch(j: dict) -> list[dict]:
    for v in (j["data"].values() if isinstance(j.get("data"), dict)
              else [j.get("data")]):
        if isinstance(v, list):
            return v
    return []


def api_status(page: Page, domain: str) -> dict:
    """Fast status from the API meta (no rendering required)."""
    h = _capture_headers(page, domain)
    j = _get(page, h, f"{BASE}/coupons?{UNCLIPPED}&page.size=1&page.offset=0")
    if not j.get("meta"):
        # _get returned a raw error envelope ({status, body})
        return {"error": "api", "detail": str(j.get("body", j))[:400]}
    info = _savings(j)
    return {
        "domain": domain,
        "clipped": info.get("clippedCount", 0),
        "available_unclipped": info.get("unclippedCount", 0),
        "clipped_savings_total": info.get("clippedSavingsTotalValue", 0),
        "expiring_count": info.get("expiringCount", 0),
        "card_at_capacity": info.get("unclippedCount", 0) > 0
        and info.get("clippedCount", 0) >= _CARD_CAP,
    }


_CARD_CAP = 250  # server rejects further clips at ~250 (422 TooManyCouponsOnCard)


def list_all(page: Page, h: dict, filt: str = UNCLIPPED,
             page_size: int = 100) -> tuple[list[dict], dict]:
    """Paginate the full coupon list. Returns (coupons, savings_info)."""
    out: list[dict] = []
    info: dict = {}
    offset = 0
    while True:
        j = _get(page, h,
                 f"{BASE}/coupons?{filt}&page.size={page_size}&page.offset={offset}")
        batch = _batch(j)
        if j.get("meta"):
            info = _savings(j)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
        time.sleep(0.25)
    return out, info


def _is_points(c: dict) -> bool:
    return any(s.get("name") == "POINTS" for s in c.get("specialSavings", []))


def clip_all(page: Page, domain: str, category: str | None = None,
             free_space: bool = False) -> dict:
    """Clip every available (unclipped, active) coupon via the API.

    category: optional department name (e.g. "Produce") to limit the run.
    free_space: when the card is at capacity, unclip the lowest-value
    already-clipped coupons (that we previously clipped, per the ledger) to
    make room, then clip the new ones. Off by default.

    Points events (no monetary value) are clipped first, then by value desc.
    Idempotent: the server rejects double-clips; the ledger tracks what was
    new *this run* for reporting.
    """
    ensure_state_dir()
    ledger = Ledger.load()
    h = _capture_headers(page, domain)

    result = {
        "clipped": 0, "failed": 0, "new": [], "skipped": 0,
        "capacity_reached": False, "freed": [], "total_available": 0,
        "ledger_pruned": 0, "domain": domain,
    }

    # Server truth first: the set of coupons actually on the card right now.
    clipped_now, _info0 = list_all(page, h, ACTIVE)
    before_ids = {str(c["id"]) for c in clipped_now if c.get("addedToCard")}
    # Prune phantom ledger entries the server never accepted (e.g. from an
    # earlier DOM-based run that over-recorded at the card cap).
    for cid in [i for i in list(ledger.clipped) if i not in before_ids]:
        del ledger.clipped[cid]
        result["ledger_pruned"] += 1

    coupons, _info1 = list_all(page, h, UNCLIPPED)
    # NOTE: the server's filter.status=unclipped is unreliable — it returns
    # the full active list. The per-coupon addedToCard flag is the truth.
    coupons = [c for c in coupons if not c.get("addedToCard")]
    result["total_available"] = len(coupons)

    if category:
        want = category.strip().lower()
        coupons = [c for c in coupons
                   if any(want in (x or "").lower() for x in c.get("categories", []))]

    def sort_key(c: dict):
        return (0 if _is_points(c) else 1, -(c.get("value") or 0))

    target = sorted(coupons, key=sort_key)

    cap_hits = 0
    for c in target:
        cid = str(c["id"])
        status, code = _clip(page, h, "CLIP", cid)
        if status == 200:
            ledger.mark(cid, name=c.get("title", ""), when=_ts())
            result["clipped"] += 1
            time.sleep(0.4)
        elif "TooManyCouponsOnCard" in code:
            cap_hits += 1
            if free_space and cap_hits == 1:
                # make room: unclip our lowest-value clipped coupons
                freed = _free_space(page, h, ledger, result)
                if freed == 0:
                    break
                # retry this coupon, then continue
                status2, _code2 = _clip(page, h, "CLIP", cid)
                if status2 == 200:
                    ledger.mark(cid, name=c.get("title", ""), when=_ts())
                    result["clipped"] += 1
                    time.sleep(0.4)
                    continue
                break
            result["capacity_reached"] = True
            break
        else:
            # 400 on an already-added coupon (double clip) is a skip, not a
            # failure; anything else is a real error.
            if status == 400:
                ledger.mark(cid, name=c.get("title", ""), when=_ts())
                continue
            result["failed"] += 1
            if result["failed"] >= 5:
                break
        if result["failed"] >= 5:
            break

    # Final truth from the server: what is on the card now, and what's NEW
    # this run (after_ids - before_ids), not from our local bookkeeping.
    clipped_after, fin = list_all(page, h, ACTIVE)
    after_ids = {str(c["id"]) for c in clipped_after if c.get("addedToCard")}
    name_by_id = {str(c["id"]): c.get("title", "") for c in clipped_after}
    result["new"] = [name_by_id.get(i, i) for i in sorted(after_ids - before_ids)]
    if fin:
        result["clipped_now_total"] = fin.get("clippedCount", 0)
        result["unclipped_remaining"] = fin.get("unclippedCount", 0)
        result["clipped_savings_total"] = fin.get("clippedSavingsTotalValue", 0)

    ledger.save()
    _write_last_run(domain, result)
    return result


def _free_space(page: Page, h: dict, ledger: Ledger, result: dict) -> int:
    """Unclip our lowest-value clipped coupons (ledger entries that the
    server confirms are on the card) until there is room for one more.
    Never touches coupons the user clipped manually."""
    clipped, _ = list_all(page, h, ACTIVE)
    by_id = {str(c["id"]): c for c in clipped}
    ours = [by_id[i] for i in ledger.clipped if i in by_id]
    ours.sort(key=lambda c: c.get("value") or 0)
    freed = 0
    for c in ours:
        cid = str(c["id"])
        status, _code = _clip(page, h, "UNCLIP", cid)
        if status == 200:
            del ledger.clipped[cid]
            result["freed"].append(c.get("title") or cid)
            freed += 1
            time.sleep(0.4)
        # stop freeing once the card count drops below cap
        j = _get(page, h, f"{BASE}/coupons?{UNCLIPPED}&page.size=1&page.offset=0")
        fin = _savings(j) if j.get("meta") else {}
        if fin.get("clippedCount", 999) < _CARD_CAP:
            break
    return freed


def clip_status(page: Page, domain: str) -> dict:
    """Full status: totals + unclipped breakdown by department."""
    from collections import Counter

    h = _capture_headers(page, domain)
    coupons, info = list_all(page, h, UNCLIPPED)
    cats: Counter = Counter()
    for c in coupons:
        for x in c.get("categories", []):
            cats[x] += 1
    return {
        "domain": domain,
        "clipped": info.get("clippedCount", 0),
        "available_unclipped": info.get("unclippedCount", 0),
        "clipped_savings_total": info.get("clippedSavingsTotalValue", 0),
        "expiring_count": info.get("expiringCount", 0),
        "fetched": len(coupons),
        "unclipped_by_category": dict(cats.most_common()),
        "sample": [{"id": str(c["id"]), "title": c.get("title", ""),
                    "category": (c.get("categories") or ["?"])[0]}
                   for c in coupons[:25]],
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
    """Dump API responses for selector/param tuning when Kroger changes."""
    ensure_state_dir()
    h = _capture_headers(page, domain)
    j = _get(page, h, f"{BASE}/coupons?{UNCLIPPED}&page.size=5&page.offset=0")
    out_path.write_text(json.dumps(j, indent=2)[:200_000])
    meta = {"url": page.url, "title": page.title(),
            "note": "If this looks wrong, update BASE/filters in coupons.py"}
    out_path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2))
