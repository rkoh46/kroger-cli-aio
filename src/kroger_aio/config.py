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

# The domain to run against. Kroger corporate; Ralphs/Dillons/etc. are the same
# platform under their own host.
DEFAULT_DOMAIN = "kroger.com"


def ensure_state_dir() -> None:
    KROGER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Ledger:
    """Tracks which coupons have been clipped, so clip-all is idempotent."""

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

    def mark(self, coupon_id: str, name: str = "", price: str = "", when: str = "") -> None:
        self.clipped[coupon_id] = {
            "name": name,
            "price": price,
            "clipped_at": when,
        }

    def new_since(self, prev_ids: set[str]) -> list[str]:
        return [cid for cid in self.clipped if cid not in prev_ids]


@dataclass
class AccountProfile:
    """Cached account info, used to personalize output and the survey."""

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
