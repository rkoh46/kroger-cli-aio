"""Kroger 2026 feedback survey (50 fuel points).

The 2020 flow (krogerstoresfeedback.com) is dead. In 2026 Kroger runs the
survey on Qualtrics:  https://www.kroger.com/feedback
        ->  https://8451cx.az1.qualtrics.com/jfe/form/SV_3PoB79U0AFELzMy

Verified live (probe_survey_walk3.py):
  * Entry-ID path, screen 1:  #Q_lang, yes/no #QR~QID95~{1,2}, 6 entry-ID
    segments #QR~QID75~1~{1..6}~TEXT, date selects
    #QR~QID1213372666#{{1,2,3}}~1 (Month/Day/Year), time selects
    #QR~QID76#{{1,2,3}}~1 (Hour/Minute/AM-PM)
  * No-ID path: store phone #QR~QID96 + same date/time selects
  * Question screens advance via #NextButton (input type=button); the final
    screen has #SubmitButton instead.
  * ~23 question screens; generic answering (radio=first, select=mid,
    textarea=canned) advances through all of them.

Qualtrics field IDs contain '~' and '#' — NOT CSS-safe. Always use
document.getElementById / locator('[id="..."]'), never '#id'.

Completion cadence: Kroger allows ONE 50-pt survey per 7 days per account, so
this is interactive (needs a recent receipt's Entry ID), not a cron job.
"""

from __future__ import annotations

import json
import time

from playwright.sync_api import Page

from .config import KROGER_STATE_DIR, ensure_state_dir

FEEDBACK_URL = "https://www.kroger.com/feedback"

_SURVEY_STATE = KROGER_STATE_DIR / "survey.json"

# Canned open-comment answers (generic, safe, non-committal).
_COMMENT = "Clean store and friendly staff. Checkout was quick. Would shop again."


def _last_completion() -> dict | None:
    try:
        return json.loads(_SURVEY_STATE.read_text()).get("last")
    except Exception:
        return None


def _record_completion(entry: str | None) -> None:
    ensure_state_dir()
    prev = {}
    try:
        prev = json.loads(_SURVEY_STATE.read_text())
    except Exception:
        pass
    prev["last"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "entry": (entry or "")[:20]}
    _SURVEY_STATE.write_text(json.dumps(prev, indent=2))


def _click_id(page: Page, el_id: str) -> bool:
    return page.evaluate(
        f"() => {{ const el = document.getElementById({json.dumps(el_id)}); "
        f"if (el) {{ el.click(); return true; }} return false; }}")


def _fill_id(page: Page, el_id: str, value: str) -> bool:
    try:
        page.fill(f"[id={json.dumps(el_id)}]", value)
        return True
    except Exception:
        return False


def _select_id(page: Page, el_id: str, index: int) -> bool:
    try:
        n = page.evaluate(
            f"() => document.getElementById({json.dumps(el_id)}).options.length")
        if n:
            page.select_option(f"[id={json.dumps(el_id)}]", index=min(index, n - 1))
        return True
    except Exception:
        return False


def _select_id_year(page: Page, el_id: str, year: int) -> bool:
    """Pick the option whose label equals `year`; fall back to the last one."""
    try:
        idx = page.evaluate(
            f"() => {{ const el = document.getElementById({json.dumps(el_id)}); "
            f"for (let i = 0; i < el.options.length; i++) "
            f"if (el.options[i].text.trim() === {json.dumps(str(year))}) return i; "
            f"return -1; }}")
        if idx is not None and idx >= 0:
            page.select_option(f"[id={json.dumps(el_id)}]", index=idx)
            return True
        return _select_id(page, el_id, 9999)  # clamp -> last option
    except Exception:
        return False


def _visible_controls(page: Page) -> list[dict]:
    return page.evaluate("""() => {
        const out = [];
        for (const el of document.querySelectorAll('input, select, textarea')) {
            out.push({tag: el.tagName.toLowerCase(), type: el.type || '',
                      id: el.id || '', vis: el.offsetParent !== null});
        }
        return out;
    }""")


def _auto_answer(page: Page) -> None:
    """Answer the current screen generically (verified to advance all
    ~23 question screens)."""
    for c in _visible_controls(page):
        cid = c["id"]
        if not c.get("vis"):
            continue
        if c["tag"] == "select":
            _select_id(page, cid, 2)
        elif c["type"] in ("radio", "checkbox"):
            _click_id(page, cid)
        elif c["tag"] == "textarea":
            _fill_id(page, cid, _COMMENT)
        elif c["type"] in ("text", "tel", "search"):
            cur = page.evaluate(
                f"() => (document.getElementById({json.dumps(cid)}).value || '').length")
            if not cur:
                _fill_id(page, cid, "42")
        time.sleep(0.15)


def complete_survey(page: Page, domain: str, entry_id: str | None,
                    date_ymd: str, time_hm: str, phone: str = "",
                    auto: bool = True) -> dict:
    """Complete the 2026 feedback survey. Interactive by design.

    entry_id: '12345-67890-12-3456-78-90' (6 segments, from the receipt).
              Omit to use the store-phone path (phone required).
    date_ymd: '2026-10-06'   time_hm: '14:30'   phone: 10 digits.
    """
    result: dict = {"started": False, "finished": False, "stopped_at": "",
                    "screens": 0, "error": ""}

    m = int(date_ymd[:4]); md = int(date_ymd[5:7]); dy = int(date_ymd[8:10])
    hh = int(time_hm[:2]); mm = int(time_hm[3:6])
    is_pm = "PM" if hh >= 12 else "AM"
    hour12 = hh % 12 or 12

    page.goto(FEEDBACK_URL, wait_until="domcontentloaded", timeout=60_000)
    time.sleep(12)
    if "qualtrics" not in page.url:
        result["error"] = f"feedback page did not redirect (url={page.url[:90]})"
        return result
    result["started"] = True

    # language — select by option TEXT (value may differ) and fire change
    page.evaluate("""() => {
        const sel = document.getElementById('Q_lang');
        if (!sel) return;
        for (const o of sel.options) {
            if (/english/i.test(o.text + ' ' + o.value)) {
                sel.value = o.value;
                sel.dispatchEvent(new Event('change', {bubbles: true}));
                return;
            }
        }
    }""")
    time.sleep(2)

    # screen 1
    if entry_id:
        _click_id(page, "QR~QID95~1")  # Yes
        segs = [s for s in entry_id.replace(" ", "").split("-") if s]
        for i, s in enumerate(segs[:6], start=1):
            if not _fill_id(page, f"QR~QID75~1~{i}~TEXT", s):
                result["error"] = f"could not fill entry-id segment {i}"
                return result
    else:
        _click_id(page, "QR~QID95~2")  # No
        if len(phone) < 10:
            result["error"] = "no entry id and no valid phone for the no-ID path"
            return result
        _fill_id(page, "QR~QID96", phone[:10])

    # date + time selects (shared by both paths)
    _select_id(page, "QR~QID1213372666#1~1", md - 1)     # Month (Jan=0)
    _select_id(page, "QR~QID1213372666#2~1", dy - 1)     # Day (1st=0)
    _select_id_year(page, "QR~QID1213372666#3~1", m)     # Year (by label)
    _select_id(page, "QR~QID76#1~1", hour12 - 1)          # Hour (1-based)
    _select_id(page, "QR~QID76#2~1", mm)                  # Minute
    _select_id(page, "QR~QID76#3~1", 1 if is_pm else 0)   # AM/PM
    time.sleep(1)

    if not _click_id(page, "NextButton"):
        result["error"] = "could not advance past screen 1 (entry id/date invalid?)"
        return result
    time.sleep(5)

    # question screens: advance until SubmitButton appears (final screen) or
    # we get stuck (validation we can't satisfy) — then hand back to the user.
    stuck = 0
    for screen in range(2, 40):
        result["screens"] = screen
        has_next = page.evaluate("() => !!document.getElementById('NextButton')")
        has_submit = page.evaluate("() => !!document.getElementById('SubmitButton')")
        if has_submit and not has_next:
            result["stopped_at"] = "final-screen"
            if auto:
                # answer the last screen's visible controls, then submit
                _auto_answer(page)
                time.sleep(1)
                _click_id(page, "SubmitButton")
                time.sleep(8)
                body = page.evaluate("() => document.body.innerText.slice(0, 400)")
                result["finished"] = True
                result["final_text"] = body
            else:
                result["error"] = "final screen reached — finish/submit manually"
            break
        if not has_next:
            result["stopped_at"] = f"screen-{screen}"
            result["error"] = "no Next/Submit found on this screen (stuck)"
            break
        _auto_answer(page)
        time.sleep(1)
        before_url = page.url
        _click_id(page, "NextButton")
        time.sleep(5)
        if page.url == before_url and page.evaluate(
                "() => !!document.getElementById('NextButton')"):
            # still on the same screen — maybe a validation error we can't fix
            stuck += 1
            if stuck >= 3:
                errs = page.evaluate("""() => [...document.querySelectorAll(
                    '.dqi__error-message')].map(e => (e.innerText||'').trim())
                    .filter(Boolean).slice(0, 3)""")
                result["stopped_at"] = f"screen-{screen}"
                result["error"] = f"validation stuck: {errs or 'unknown'}"
                break
        else:
            stuck = 0

    if result.get("finished"):
        _record_completion(entry_id)
    page.screenshot(path=str(KROGER_STATE_DIR / "survey_last.png"))
    return result
