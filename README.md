# kroger-aio

Kroger **all-in-one** CLI: clip **all** digital coupons (not just the first
150), check what's new, and track your account — modernized for 2026 and
built for macOS.

> A modern, Hermes-ready fork of
> [Shmakov/kroger-cli](https://github.com/Shmakov/kroger-cli) by
> **Nikolay Shmakov** — all original credit where it's due. Thanks, Nik.

## What changed vs. the original

| | original | this |
|---|---|---|
| Python | 3.8, `requirements.txt` | 3.13+/3.14, PEP 621 `pyproject.toml` + `uv` |
| Browser | `pyppeteer` (2019, EOL) | **Playwright** (bundled Chromium, headed — Kroger's WAF blocks headless) |
| Login | password typed every run | **login once**, persistent session — daily runs are password-free |
| Coupons | first 150 by relevance | **all** coupons, paginated, **idempotent** (ledger of what's clipped) |
| OS | Windows executable builds | macOS-only (Windows support removed on purpose) |
| Output | human only | `--json` on every command + exit codes for automation |
| Config | `config.ini` plaintext in repo dir | state in `~/.kroger-aio/`, credentials in `~/.hermes/.env` — never in the repo |

## Requirements

- macOS (GUI available — see "How it runs" below)
- [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- Playwright Chromium (installed automatically by `install.sh`)

## How it runs (important)

Kroger's edge firewall (Akamai) resets connections from headless/automated
browser fingerprints. So `kroger-aio` drives a **visible Chromium window** —
the same approach the original tool used. Each command opens a window for the
duration of the operation, then closes it.

You sign in **once** (`kroger-aio login`); the session cookies persist in
`~/.kroger-aio/browser-profile/`, so every later run is password-free. The
password is only ever used by `login` and never written to disk by this tool.

## Setup

```bash
./install.sh
```

Put your Kroger credentials in `~/.hermes/.env` (or any env file you load
before running):

```
KROGER_EMAIL=you@example.com
KROGER_PASSWORD=***
```

Then sign in once (a visible Chromium window opens; the session is persisted):

```bash
uv run kroger-aio login
```

> The window closes automatically when sign-in completes. If Kroger throws a
> CAPTCHA or 2FA, solve it in the window — the script waits for it.

## Usage

```bash
uv run kroger-aio clip         # clip every available coupon (idempotent)
uv run kroger-aio status       # clipped vs. still available
uv run kroger-aio points       # rewards balance
uv run kroger-aio profile      # account info
uv run kroger-aio purchases    # yearly spend summary
uv run kroger-aio discover     # dump live coupons page (selector tuning)
```

Every command accepts `--json` (machine output) and `--domain`
(`ralphs.com`, `dillons.com`, `kingsoopers.com`, …).

**Exit codes:** `0` ok · `2` session expired (run `login`) · `3` failure.

## Hermes integration

This tool is designed to be driven by a Hermes agent:

- A **skill** (`kroger-coupons`) teaches any Hermes session to run it and
  interpret the exit codes.
- A **cron job** runs `clip --json` on a schedule; when nothing new is
  clipped it stays silent, and it pings you only when new coupons are clipped
  or the session needs a re-login. (Runs need a GUI session for the visible
  window — fine when the Mac is awake and logged in; the skill handles the
  "Mac asleep / window unavailable" case by deferring and notifying.)

Just ask: *"Hey, check my Kroger coupons"* — or let the schedule handle it.

## Security

- The password is only used by `login`; everyday runs use the stored browser
  session (cookies in `~/.kroger-aio/browser-profile/`, gitignored).
- `~/.kroger-aio/`, `.env`, `config.ini`, and `credentials*` are gitignored.
- **Never commit** `~/.kroger-aio`. A secret scan is run before every push.

## Selector tuning

Kroger changes its markup. If `clip` starts failing, run:

```bash
uv run kroger-aio discover
```

then inspect `~/.kroger-aio/discover.html` + `discover.buttons.json` and update
`COUPON_SELECTORS` in `src/kroger_aio/coupons.py`.

## License & credit

Original project by Nikolay Shmakov
([github.com/Shmakov/kroger-cli](https://github.com/Shmakov/kroger-cli)).
This fork keeps the original commit history and credits the original author.
