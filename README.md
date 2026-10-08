# kroger-aio

Kroger **all-in-one** CLI: clip **all** digital coupons (not just the first
150), check what's new, and track your account — modernized for 2026.

**Platform support:**
- 🟢 **macOS** — supported; developed and tested here
- 🟡 **Linux** — supported (no platform-specific code; untested)
- 🟡 **Windows** — supported (untested — if the bundled Chromium gets
  blocked, set `KROGER_BROWSER_CHANNEL=chrome` to drive your installed Chrome)

> A modern, Hermes-ready fork of
> [Shmakov/kroger-cli](https://github.com/Shmakov/kroger-cli) by
> **Nikolay Shmakov** — all original credit where it's due. Thanks, Nik.

## What changed vs. the original

| | original | this |
|---|---|---|
| Python | 3.8, `requirements.txt` | 3.13+/3.14, PEP 621 `pyproject.toml` + `uv` |
| Browser | `pyppeteer` (2019, EOL) | **Playwright** (bundled Chromium, headed — Kroger's WAF blocks headless) |
| Login | password typed every run | **login once**, persistent session — daily runs are password-free |
| Coupons | first 150 by relevance, button-clicked | **API-driven**: paginates Kroger's own coupon API, clips everything that fits the card, **idempotent** + per-category |
| OS | Windows executable builds | **macOS tested**; Linux + Windows supported (untested) |
| Output | human only | `--json` on every command + exit codes for automation |
| Config | `config.ini` plaintext in repo dir | state in `~/.kroger-aio/` (gitignored); password typed only into the browser's own field |

## Requirements

- Any OS with a **GUI session** (a visible Chromium window is required —
  see "How it runs" below): macOS (tested), Linux / Windows (supported,
  untested)
- [uv](https://docs.astral.sh/uv/) (`brew install uv` on macOS;
  `curl -LsSf https://astral.sh/uv/install.sh | sh` on Linux;
  `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"` on Windows)
- Playwright Chromium (installed automatically by `install.sh`)

## How it works (important)

Two layers, both needed:

1. **A visible Chromium window.** Kroger's edge firewall (Akamai) resets
   connections from headless/automated browser fingerprints, so `kroger-aio`
   drives a headed Chromium against a persistent profile. Direct HTTP requests
   from plain Python also get reset — but requests made *from the page*
   (in-page `fetch`) pass, which is what the engine uses.
2. **The internal coupon API.** The 2026 site exposes
   `GET/POST /atlas/v1/savings-coupons/v1/...` with pagination, category
   filters, and the exact clip/unclip action the page itself uses. The engine
   replays the page's own request headers (captured once per run) and clips
   via the API — fast, uncapped by rendering, and verifiable. No button
   clicking.

You sign in **once** (`kroger-aio login`); the session cookies persist in
`~/.kroger-aio/browser-profile/`, so every later run is password-free. The
password is only ever used by `login` and never written to disk by this tool.

### The card capacity limit

Kroger caps the number of digital coupons per loyalty card (249–250,
observed). When the card is full, the server rejects new clips with
`422 TooManyCouponsOnCard`; `kroger-aio clip` then stops cleanly and reports
how many remain available. Options:

- wait — old coupons expire every week and free space (re-run `clip`);
- `kroger-aio clip --free-space` — bring the card down to the target and
  value-swap this tool's own lowest-value clips for higher-value new ones
  (it never touches coupons you clipped yourself manually, per the ledger);
- `kroger-aio clip --category "Meat & Seafood"` — only clip one department.

### The target cap (default 239)

`clip` stops at **239** by default — deliberately *before* the server's ~250
cap. This keeps a buffer so runs never end in a wall of 422 rejections at the
hard limit (and stays clear of the "maximum number of offers" state the
website complains about). The headroom is filled by the next run, so nothing
is left on the table — space frees as coupons expire weekly. Override with
`--target 245` or `KROGER_TARGET=245`.

## Setup

```bash
./install.sh
```

Then sign in once. A visible Chromium window opens with your email pre-filled
(if you set `KROGER_EMAIL` in your env or it was captured on a prior run):

```bash
uv run kroger-aio login
```

**Type your Kroger password into the password field in that window.** That
browser field is the secure prompt — the password goes straight to Kroger and
never passes through this program, the shell, env, or disk. The window
submits automatically once the field has text, and closes when done. If a
CAPTCHA or 2FA appears, solve it in the window.

The session (cookies) persists in `~/.kroger-aio/browser-profile/`, so every
later run is password-free.

## Usage

```bash
uv run kroger-aio clip         # clip new coupons up to the target (239)
uv run kroger-aio clip --category Produce      # one department only
uv run kroger-aio clip --free-space            # trim to target + value-swap in better deals
uv run kroger-aio clip --target 245            # override the stop cap
uv run kroger-aio status       # card totals + unclipped by department
uv run kroger-aio points       # rewards balance
uv run kroger-aio profile      # account info
uv run kroger-aio purchases    # purchases page summary
uv run kroger-aio survey --date 2026-10-06 --time 14:30 --entry-id 12345-67890-12-3456-78-90
uv run kroger-aio menu         # interactive numbered menu (like the original)
uv run kroger-aio discover     # dump the live API response (tuning)
```

Every command accepts `--json` (machine output) and `--domain`
(`ralphs.com`, `dillons.com`, `kingsoopers.com`, …).

**Exit codes:** `0` ok · `2` session expired (run `login`) · `3` failure.

### Interactive menu

`kroger-aio menu` brings back the original CLI's numbered prompt:

```
1 - Display account info        3 - Purchases Summary
2 - Clip all digital coupons    4 - Points Balance
5 - Complete Kroger's Survey    8 - Re-Enter username/password
9 - Exit
```

### The receipt survey (50 fuel points)

The 2020 survey host (`krogerstoresfeedback.com`) is dead. In 2026 Kroger
runs the survey on **Qualtrics** at `kroger.com/feedback`. `kroger-aio
survey` drives it for you: it fills the receipt's Entry ID (or store phone),
visit date/time, answers every question screen, and submits — worth **50 fuel
points, once per 7 days**. You'll need a recent receipt's **Entry ID** (shown
on the receipt) and visit date/time. `--manual` stops at the final screen so
you can finish by hand.

```bash
# with the receipt's Entry ID
uv run kroger-aio survey --date 2026-10-06 --time 14:30 \
    --entry-id 12345-67890-12-3456-78-90
# or via the store-phone path (no Entry ID)
uv run kroger-aio survey --date 2026-10-06 --time 14:30 --phone 3105551234
```

## Hermes integration

This tool is designed to be driven by a Hermes agent:

- A **skill** (`kroger-coupons`) teaches any Hermes session to run it and
  interpret the exit codes.
- A **cron job** runs `clip --json` on a schedule; when nothing new is
  clipped it stays silent, and it pings you only when new coupons are clipped
  or the session needs a re-login. (Runs need a GUI session for the visible
  window — fine when the desktop is awake and logged in; the skill handles
  the "machine asleep / window unavailable" case by deferring and notifying.)

Just ask: *"Hey, check my Kroger coupons"* — or let the schedule handle it.

## Security

- The password is only used by `login`; everyday runs use the stored browser
  session (cookies in `~/.kroger-aio/browser-profile/`, gitignored).
- `~/.kroger-aio/`, `.env`, `config.ini`, and `credentials*` are gitignored.
- **Never commit** `~/.kroger-aio`. A secret scan is run before every push.

## If Kroger changes something

If `clip`/`status` start failing, run:

```bash
uv run kroger-aio discover
```

It dumps the live coupon-API response to `~/.kroger-aio/discover.json`.
Compare the field names against `src/kroger_aio/coupons.py` (endpoints,
`filter.*` params, `addedToCard`, clip action codes) and update accordingly.

## License & credit

Original project by **Nikolay Shmakov** —
[github.com/Shmakov/kroger-cli](https://github.com/Shmakov/kroger-cli).
This is an independent modernized fork (fresh history); it keeps the original
author's credit for the design and the idea, and the original repo remains the
canonical upstream to diff against.
