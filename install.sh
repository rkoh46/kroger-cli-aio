#!/usr/bin/env bash
# kroger-aio: one-time setup on macOS.
# Requires: macOS with a GUI session, and `uv` (brew install uv).
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Install it with: brew install uv" >&2
  exit 1
fi

echo "==> Creating virtualenv with $(cat .python-version)"
uv sync

echo "==> Installing Playwright Chromium (first run only)"
uv run playwright install chromium

echo "==> Done."
echo
echo "Next steps:"
echo "  1. Sign in once (a visible Chromium window opens; type your password"
echo "     into the password field in that window):"
echo "       uv run kroger-aio login"
echo "     (Optional: export KROGER_EMAIL=you@example.com to pre-fill the email.)"
echo "  2. Clip everything:"
echo "       uv run kroger-aio clip"
echo
echo "Daily use: 'uv run kroger-aio clip'  (no password needed after login)."
echo "State lives in ~/.kroger-aio (gitignored). Never commit that directory."
