"""Kroger all-in-one CLI (modernized, macOS-only).

Clips all digital coupons and tracks account state using Playwright against
the user's real Chrome. Login runs once in a visible window; daily runs are
headless and reuse the persisted session, so no password is needed afterward.
"""

__version__ = "1.0.0"
