"""Offline tests for the target-cap clip logic (no browser, no real clips).

Run: uv run python tests/test_target_cap.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import kroger_aio.coupons as cp

CAP = 249  # simulated server cap


class FakeLedger:
    def __init__(self, clipped: dict):
        self.clipped = clipped

    def mark(self, cid, name="", when=""):
        self.clipped[cid] = {"name": name, "when": when}

    def save(self):
        pass


def make_world(card_start: int, ours: list[str], unclipped: list[dict]):
    """Fake server state. `ours` = coupon ids we clipped (on the card);
    the card is padded with non-ours filler clips up to `card_start`."""
    card: dict = {i: {"id": i, "addedToCard": True, "value": 0.5} for i in ours}
    filler = 0
    while len(card) < card_start:
        key = f"filler{filler}"
        card[key] = {"id": key, "addedToCard": True, "value": 1.0}
        filler += 1
    state = {
        "card": card,
        "unclipped": {c["id"]: c for c in unclipped},
        "clips": 0,
        "unclips": 0,
        "errors": 0,
    }

    def fake_write_last_run(domain, result):
        pass  # don't clobber the user's real last-run state in tests

    def fake_capture_headers(page, domain):
        return {}

    def fake_list_all(page, h, filt, page_size=100):
        if "active" == filt and "unclipped" not in filt:  # ACTIVE
            coupons = [dict(c, id=str(c["id"])) for c in state["card"].values()]
            info = {"clippedCount": len(state["card"])}
            return coupons, info
        # UNCLIPPED (server is unreliable: returns the full active list)
        all_c = [dict(c, id=str(c["id"])) for c in state["card"].values()]
        all_c += [dict(c, id=str(c["id"])) for c in state["unclipped"].values()]
        info = {"clippedCount": len(state["card"]),
                "unclippedCount": len(state["unclipped"])}
        return all_c, info

    def fake_get(page, h, url, attempts=3):
        return {"meta": {"coupons": {"userSavingsInfoByType": {"standard":
                {"clippedCount": len(state["card"])}}}}}

    def fake_clip(page, h, action, coupon_id):
        if action == "CLIP":
            if coupon_id in state["card"]:
                return 400, "INVALID_REQUEST"
            if len(state["card"]) >= CAP:
                state["errors"] += 1
                return 422, "TooManyCouponsOnCard"
            c = dict(state["unclipped"][coupon_id])
            c["addedToCard"] = True  # the real API returns it on the card now
            state["card"][coupon_id] = c
            state["clips"] += 1
            return 200, ""
        # UNCLIP
        if coupon_id in state["card"]:
            del state["card"][coupon_id]
            state["unclips"] += 1
            return 200, ""
        return 422, "CantUnloadCoupon"

    return state, {
        "_capture_headers": fake_capture_headers,
        "list_all": fake_list_all,
        "_get": fake_get,
        "_clip": fake_clip,
        "_write_last_run": fake_write_last_run,
    }


def apply(fakes):
    for name, fn in fakes.items():
        setattr(cp, name, fn)


def restore():
    # re-import fresh module state
    import importlib
    importlib.reload(cp)


def unclipped_pool(n: int) -> list[dict]:
    return [{"id": f"u{i:04d}", "title": f"U{i}", "value": 1 + i * 0.5,
             "categories": ["Produce"], "specialSavings": []} for i in range(n)]


def test_plain_clip_never_reaches_cap():
    """Card above target: plain clip does nothing, no 422s."""
    state, fakes = make_world(249, [f"o{i}" for i in range(249)], unclipped_pool(20))
    apply(fakes)
    cp.Ledger.load = staticmethod(lambda: FakeLedger({k: {} for k in state["card"]}))  # type: ignore[method-assign]
    r = cp.clip_all(None, "ralphs.com")
    restore()
    assert r["clipped"] == 0, r
    assert r["target_reached"] is True, r
    assert r["target"] == 239, r
    assert state["clips"] == 0 and state["unclips"] == 0, state
    assert state["errors"] == 0, "no 422s allowed"
    assert r["new"] == [], r


def test_plain_clip_stops_at_target():
    """Card at 230 with room: clips exactly 9 (230 -> 239), never 10."""
    state, fakes = make_world(230, [f"o{i}" for i in range(230)], unclipped_pool(50))
    apply(fakes)
    cp.Ledger.load = staticmethod(lambda: FakeLedger({k: {} for k in state["card"]}))  # type: ignore[method-assign]
    r = cp.clip_all(None, "ralphs.com")
    restore()
    assert r["clipped"] == 9, r
    assert len(state["card"]) == 239, len(state["card"])
    assert state["errors"] == 0, "no 422s allowed"
    assert r["target_reached"] is True, r
    assert len(r["new"]) == 9, r


def test_free_space_trims_to_target_then_swaps():
    """Card at cap, we own 15 low clips: trim 10 down to 239, then value-swap
    in higher-value unclipped until nothing left is better than our lowest."""
    ours = [f"o{i}" for i in range(15)]
    state, fakes = make_world(249, ours, unclipped_pool(30))
    # unclipped pool: values 1.5 .. 15.5 — all better than our 0.5 clips
    apply(fakes)
    cp.Ledger.load = staticmethod(lambda: FakeLedger({k: {} for k in ours}))  # type: ignore[method-assign]
    r = cp.clip_all(None, "ralphs.com", free_space=True)
    restore()
    # Trim 10 of ours (249 -> 239, 15 -> 5), then 5 value-swaps: each swap
    # unclips our last remaining lowest (0.5) and clips the best candidate —
    # once all 15 of ours are gone, swaps stop. Count stays 239 throughout.
    assert len(state["card"]) == 239, len(state["card"])
    assert state["unclips"] == 15, state["unclips"]   # 10 trim + 5 swaps
    assert state["clips"] == 5, state["clips"]
    assert state["errors"] == 0, "no 422s allowed"
    assert r["target_reached"] is True, r
    assert r["clipped"] == 5, (r["clipped"], state["clips"])
    # the top candidates should be on the card, our 0.5 clips gone
    top = max((c.get("value") for c in state["card"].values()), default=0)
    assert top >= 15.0, top
    assert not any(c["value"] == 0.5 for c in state["card"].values())


def test_free_space_never_touches_user_clips():
    """Card at cap but 0 of the clips are ours: free_space does nothing
    to the card (it won't unclip user coupons)."""
    state, fakes = make_world(249, [f"usr{i}" for i in range(249)],
                              unclipped_pool(10))
    apply(fakes)
    cp.Ledger.load = staticmethod(lambda: FakeLedger({}))  # ledger: nothing ours
    r = cp.clip_all(None, "ralphs.com", free_space=True)
    restore()
    assert state["unclips"] == 0, "must not unclip user coupons"
    assert len(state["card"]) == 249, len(state["card"])
    assert r["new"] == [], r


def test_target_env_override():
    """KROGER_TARGET env is honored."""
    import os
    os.environ["KROGER_TARGET"] = "235"
    state, fakes = make_world(230, [f"o{i}" for i in range(230)], unclipped_pool(50))
    apply(fakes)
    cp.Ledger.load = staticmethod(lambda: FakeLedger({k: {} for k in state["card"]}))  # type: ignore[method-assign]
    r = cp.clip_all(None, "ralphs.com")
    restore()
    os.environ.pop("KROGER_TARGET", None)
    assert r["clipped"] == 5, r
    assert len(state["card"]) == 235, len(state["card"])
    assert r["target"] == 235, r


if __name__ == "__main__":
    test_plain_clip_never_reaches_cap()
    test_plain_clip_stops_at_target()
    test_free_space_trims_to_target_then_swaps()
    test_free_space_never_touches_user_clips()
    test_target_env_override()
    print("ALL TARGET-CAP TESTS PASSED")
