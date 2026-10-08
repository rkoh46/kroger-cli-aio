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
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Page

from .config import LAST_RUN_PATH, Ledger, ensure_state_dir

BASE = "/atlas/v1/savings-coupons/v1"
UNCLIPPED = "filter.status=unclipped&filter.status=active"
ACTIVE = "filter.status=active"

# Stop clipping before the server cap so runs never end in a wall of
# 422 rejections (which reads as hammering at their edge). Override with
# --target or KROGER_TARGET.
DEFAULT_TARGET = 239


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
             free_space: bool = False, target: int | None = None) -> dict:
    """Clip every available (unclipped, active) coupon via the API.

    category: optional department name (e.g. "Produce") to limit the run.
    free_space: when the card is at capacity, unclip the lowest-value
    already-clipped coupons (that we previously clipped, per the ledger) to
    make room, then clip the new ones. Off by default.
    target: stop clipping once the card reaches this count (default
    DEFAULT_TARGET=239, or KROGER_TARGET env) — keeps a buffer under the
    server's ~250 cap so runs never end at the hard limit.

    Points events (no monetary value) are clipped first, then by value desc.
    Idempotent: the server rejects double-clips; the ledger tracks what was
    new *this run* for reporting.
    """
    if target is None:
        target = int(os.environ.get("KROGER_TARGET", DEFAULT_TARGET))
    ensure_state_dir()
    ledger = Ledger.load()
    h = _capture_headers(page, domain)

    result = {
        "clipped": 0, "failed": 0, "new": [], "skipped": 0,
        "capacity_reached": False, "target_reached": False,
        "target": target, "freed": [], "total_available": 0,
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

    start_count = len(before_ids)
    count = start_count  # running estimate of coupons on the card

    # Enforce the target cap up front (free_space only): if the card is above
    # the target, unclip our lowest-value clips down to it first.
    if free_space and start_count > target:
        need = start_count - target
        for c in _ours_on_card(page, h, ledger)[:need]:
            if not _unclip_one(page, h, ledger, result, c):
                break
        # Re-derive from server truth — the trim may not have fully worked.
        _clipped_now2, _ = list_all(page, h, ACTIVE)
        count = sum(1 for c in _clipped_now2 if c.get("addedToCard"))

    room = max(0, target - count)
    candidates = sorted(coupons, key=sort_key)
    # Normal clip: only as many as fit under the target. free_space: the full
    # list (it manages the count itself, clipping then value-swapping).
    pool = candidates if free_space else candidates[:room]

    ours_list: list | None = None  # cached ours (lowest first) for swaps
    for c in pool:
        cid = str(c["id"])
        if free_space and count >= target:
            # At the target: value-swap — unclip our lowest, clip this better
            # one (count stays the same). Candidates are sorted by value
            # desc, so once this one isn't better than our lowest, stop.
            if ours_list is None:
                ours_list = _ours_on_card(page, h, ledger)
            if not ours_list:
                break
            low = ours_list[0]
            if not _is_points(c) and (c.get("value") or 0) <= (low.get("value") or 0):
                break
            if _unclip_one(page, h, ledger, result, low):
                ours_list.pop(0)
                status, _code = _clip(page, h, "CLIP", cid)
                if status == 200:
                    ledger.mark(cid, name=c.get("title", ""), when=_ts())
                    ledger.save()  # crash-safe
                    result["clipped"] += 1
                    time.sleep(0.4)
            else:
                break  # can't unclip our lowest; next run retries
            continue
        status, code = _clip(page, h, "CLIP", cid)
        if status == 200:
            ledger.mark(cid, name=c.get("title", ""), when=_ts())
            ledger.save()  # crash-safe
            result["clipped"] += 1
            count += 1
            time.sleep(0.4)
        elif "TooManyCouponsOnCard" in code:
            # Defensive: the pool was already trimmed to `room`, so this
            # should not happen (server count drift).
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
    if count >= target:
        result["target_reached"] = True

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


def _ours_on_card(page: Page, h: dict, ledger: Ledger) -> list[dict]:
    """Our (ledger-tracked) clips that the server confirms are on the card,
    lowest value first. Never touches coupons the user clipped manually."""
    clipped, _ = list_all(page, h, ACTIVE)
    by_id = {str(c["id"]): c for c in clipped}
    ours = [by_id[i] for i in ledger.clipped if i in by_id]
    ours.sort(key=lambda c: c.get("value") or 0)
    return ours


def _unclip_one(page: Page, h: dict, ledger: Ledger, result: dict,
                c: dict) -> bool:
    cid = str(c["id"])
    status, _code = _clip(page, h, "UNCLIP", cid)
    if status == 200:
        ledger.clipped.pop(cid, None)
        ledger.save()  # crash-safe
        result["freed"].append(c.get("title") or cid)
        time.sleep(0.4)
        return True
    return False


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
