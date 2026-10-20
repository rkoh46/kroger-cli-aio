# kroger-aio for Windows

A Windows version of kroger-aio: clip all available Kroger digital coupons,
track your account, and automate the feedback survey — from a terminal
window or an interactive menu.

## What's included

- `kroger-aio.exe` — the main application
- `install-browser.exe` — Playwright Chromium browser installer (run once)
- `run.bat` — quick-start batch script

## Quick start (double-click to begin)

1. **Install the Playwright browser (one-time):** Double-click `install-browser.exe`.
   This downloads the Chromium browser that Kroger automation uses. Takes ~2 min.

2. **Run the app:** Double-click `run.bat` or open a terminal and run:
   ```bat
   kroger-aio.exe menu
   ```

3. **Log in:** Choose option `8` in the menu. A Chrome window opens — sign in with
   your Kroger credentials. Done — your session persists for future runs.

## Usage

```bat
:: Log in to your Kroger account
kroger-aio.exe login --email you@example.com

:: Clip all available digital coupons
kroger-aio.exe clip

:: Check how many coupons are clipped vs. still available
kroger-aio.exe status

:: Show points balance
kroger-aio.exe points

:: Run the interactive menu
kroger-aio.exe menu
```

## Options

- `--email EMAIL` — your Kroger account email
- `--domain DOMAIN` — store domain (default: kroger.com)
- `--category NAME` — clip only coupons in this category
- `--free-space` — make room on card (unclip lowest-value coupons)
- `--target N` — stop clipping at N coupons (default: 239)

## Requirements

- Windows 10/11
- No Python or Playwright needed — everything is bundled

## Troubleshooting

- **Browser not found error:** Run `install-browser.exe` again.
- **No session / need to re-login:** Run `kroger-aio.exe login`.
- **App crashes:** Check that your Windows Defender isn't quarantining files.

## License

MIT (same as upstream kroger-cli)
