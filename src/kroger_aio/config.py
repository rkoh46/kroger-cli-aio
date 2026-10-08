"""Configuration and path helpers.

State (browser profile, clipped-coupon ledger, account profile) lives in a
single gitignored directory under the user's home so nothing sensitive ever
ends up in the repository.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

KROGER_STATE_DIR = Path(os.environ.get("KROGER_AIO_STATE", str(Path.home() / ".kroger-aio")))
BROWSER_PROFILE_DIR = KROGER_STATE_DIR / "browser-profile"
COUPONS_LEDGER_PATH = KROGER_STATE_DIR / "clipped_coupons.json"
PROFILE_PATH = KROGER_STATE_DIR / "profile.json"
LAST_RUN_PATH = KROGER_STATE_DIR / "last_run.json"

# Default store. Kroger brand sites (Ralphs, Dillons, King Soopers, ...) all
# run the same web app under a /savings/ path prefix; kroger.com does not.
DEFAULT_DOMAIN = "kroger.com"

_BRANDS_WITH_PREFIX = {
    "ralphs.com",
    "dillons.com",
    "kingsoopers.com",
    "qfc.com",
    "bakersplus.com",
    "citymarket.com",
    "frysfood.com",
    "food4less.com",
    "fredmeyer.com",
    "marianos.com",
    "metromarket.net",
    "picknsave.com",
    "smithsfoodanddrug.com",
}


def path_prefix(domain: str) -> str:
    """Route prefix for a store domain ('' for kroger.com, '/savings' for
    brand sites). Verified live against ralphs.com in 2026."""
    d = domain.lower().removeprefix("www.")
    return "/savings" if d in _BRANDS_WITH_PREFIX else ""


def coupons_url(domain: str) -> str:
    p = path_prefix(domain)
    return f"https://www.{domain.lower()}{p}/cl/coupons"


def ensure_state_dir() -> None:
    KROGER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Ledger:
    """Tracks which coupons have been clipped, so clip-all is idempotent.

    Keys are Kroger's stable numeric coupon ids (from data-testid
    "CouponTitle-<id>"), which survive re-renders and page reloads.
    """

    clipped: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls) -> Ledger:
        if COUPONS_LEDGER_PATH.exists():
            try:
                data = json.loads(COUPONS_LEDGER_PATH.read_text())
                return Ledger(clipped=data.get("clipped", {}))
            except (json.JSONDecodeError, OSError):
                pass
        return Ledger()

    def save(self) -> None:
        ensure_state_dir()
        COUPONS_LEDGER_PATH.write_text(json.dumps({"clipped": self.clipped}, indent=2))

    def is_clipped(self, coupon_id: str) -> bool:
        return coupon_id in self.clipped

    def mark(self, coupon_id: str, name: str = "", when: str = "") -> None:
        self.clipped[coupon_id] = {"name": name, "clipped_at": when}

    def new_since(self, prev_ids: set[str]) -> list[str]:
        return [cid for cid in self.clipped if cid not in prev_ids]


@dataclass
class AccountProfile:
    """Cached account info (email for pre-filling; name for display)."""

    data: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> AccountProfile:
        if PROFILE_PATH.exists():
            try:
                return AccountProfile(data=json.loads(PROFILE_PATH.read_text()))
            except (json.JSONDecodeError, OSError):
                pass
        return AccountProfile()

    def save(self) -> None:
        ensure_state_dir()
        PROFILE_PATH.write_text(json.dumps(self.data, indent=2))
