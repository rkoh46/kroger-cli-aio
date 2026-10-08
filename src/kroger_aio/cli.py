"""kroger-aio CLI (typer).

Commands:
  login         One-time interactive sign-in (visible Chrome window).
  clip          Clip all available coupons; prints new coupons. Idempotent.
  status        Report clipped vs. still-available coupons.
  points        Points / rewards balance.
  profile       Account profile info.
  purchases     Purchases summary table.
  discover      Dump live coupons-page HTML + button inventory (selector tuning).

Output: human (rich) by default, or --json for machine parsing.
Exit codes: 0 = ok, 2 = no/expired session, 3 = runtime failure.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from rich.console import Console

from . import __version__
from .account import get_points, get_profile, get_purchases
from .browser import SessionError, browser_session, run_visible_login
from .config import KROGER_STATE_DIR
from .coupons import DEFAULT_TARGET, clip_all, clip_status
from .coupons import discover as discover_dump

app = typer.Typer(
    name="kroger-aio",
    help="Kroger all-in-one: clip every digital coupon and track your account.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

EXIT_OK = 0
EXIT_SESSION = 2
EXIT_FAIL = 3


def _domain() -> str:
    return os.environ.get("KROGER_DOMAIN", "kroger.com")


def _emit(payload: dict, as_json: bool, human_lines: list[str]) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        for line in human_lines:
            console.print(line)


@app.command()
def version() -> None:
    """Print the version."""
    print(f"kroger-aio {__version__}")


@app.command()
def login(
    domain: str = typer.Option(None, "--domain", help="kroger.com, ralphs.com, ..."),
    as_json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Sign in once. Opens a visible Chromium window; session is persisted.

    The password is typed by you into the window's own masked field — it never
    passes through this program, the shell, or env.
    """
    dom = domain or _domain()
    # Persist the email (not the password) so future logins skip the prompt.
    from .config import AccountProfile, ensure_state_dir

    ensure_state_dir()
    prof = AccountProfile.load()
    username = os.environ.get("KROGER_EMAIL", "") or prof.data.get("email", "")
    if not username:
        username = typer.prompt("Kroger username (email)")
    if prof.data.get("email") != username:
        prof.data["email"] = username
        prof.save()

    ok = run_visible_login(dom, username, console=console)
    if as_json:
        print(json.dumps({"signed_in": ok}))
    raise typer.Exit(EXIT_OK if ok else EXIT_FAIL)


@app.command()
def clip(
    domain: str = typer.Option(None, "--domain", help="Store domain"),
    category: str = typer.Option(
        None, "--category", help="Only clip one department, e.g. 'Produce'"),
    free_space: bool = typer.Option(
        False, "--free-space",
        help="If the card is full, unclip our lowest-value previously-clipped "
             "coupons to make room for new ones (value-swaps when at the "
             "target)"),
    target: int = typer.Option(
        None, "--target",
        help="Stop clipping once the card reaches this many coupons "
             f"(default {DEFAULT_TARGET}, env KROGER_TARGET)"),
    as_json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Clip every available coupon (API-driven, idempotent)."""
    dom = domain or _domain()
    try:
        with browser_session(dom, require_session=True) as page:
            result = clip_all(page, dom, category=category,
                              free_space=free_space, target=target)
    except SessionError as e:
        if as_json:
            print(json.dumps({"error": "session", "detail": str(e)}))
        else:
            console.print(f"[bold red]{e}[/bold red]")
        raise typer.Exit(EXIT_SESSION)
    except Exception as e:
        if as_json:
            print(json.dumps({"error": "fail", "detail": str(e)}))
        else:
            console.print(f"[bold red]Clip failed: {e}[/bold red]")
        raise typer.Exit(EXIT_FAIL)

    new = result.get("new", [])
    payload = {**result, "domain": dom}
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        if result.get("capacity_reached"):
            console.print(
                "[yellow]Card is at its Kroger coupon capacity "
                f"({result.get('clipped_now_total')} clipped, "
                f"{result.get('unclipped_remaining')} still available).[/yellow] "
                "[dim]Re-run later when coupons expire, or use --free-space "
                "to swap in new ones.[/dim]"
            )
        elif result.get("target_reached"):
            console.print(
                f"[dim]Stopped at the --target cap of "
                f"{result.get('target')} clipped (headroom left before the "
                f"server's ~250 cap).[/dim]"
            )
        if result.get("freed"):
            console.print(f"[dim]Unclipped {len(result['freed'])} old coupon(s) "
                          "to make room.[/dim]")
        if new:
            console.print(f"[bold green]Clipped {len(new)} new coupon(s):[/bold green]")
            for name in new[:50]:
                console.print(f"  [green]+[/green] {name}")
            if len(new) > 50:
                console.print(f"  [dim]…and {len(new) - 50} more[/dim]")
        elif not result.get("capacity_reached"):
            console.print("[green]All available coupons already clipped. Nothing new.[/green]")
        console.print(
            f"[dim]Clipped this run: {result['clipped']} | "
            f"failed: {result['failed']} | "
            f"card total: {result.get('clipped_now_total', '?')}[/dim]"
        )
    raise typer.Exit(EXIT_OK)


@app.command()
def status(
    domain: str = typer.Option(None, "--domain", help="Store domain"),
    as_json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Show how many coupons are clipped vs. still available."""
    dom = domain or _domain()
    try:
        with browser_session(dom, require_session=True) as page:
            s = clip_status(page, dom)
    except SessionError as e:
        if as_json:
            print(json.dumps({"error": "session", "detail": str(e)}))
        else:
            console.print(f"[bold red]{e}[/bold red]")
        raise typer.Exit(EXIT_SESSION)
    except Exception as e:
        if as_json:
            print(json.dumps({"error": "fail", "detail": str(e)}))
        else:
            console.print(f"[bold red]{e}[/bold red]")
        raise typer.Exit(EXIT_FAIL)

    if as_json:
        print(json.dumps(s, indent=2))
    else:
        console.print(
            f"Card: [bold]{s.get('clipped', 0)}[/bold] clipped | "
            f"[yellow]{s.get('available_unclipped', 0)}[/yellow] still available | "
            f"saved [green]${s.get('clipped_savings_total', 0):.2f}[/green]"
        )
        if s.get("unclipped_by_category"):
            console.print("[bold]Unclipped by department:[/bold]")
            for cat, n in list(s["unclipped_by_category"].items())[:30]:
                console.print(f"  • {cat}: {n}")
        if s.get("sample"):
            console.print("[bold]Sample (first 15):[/bold]")
            for c in s["sample"][:15]:
                console.print(f"  • {c['title'] or c['id']}  [dim]({c['category']})[/dim]")
    raise typer.Exit(EXIT_OK)


@app.command()
def points(
    domain: str = typer.Option(None, "--domain"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Rewards / points balance."""
    dom = domain or _domain()
    try:
        with browser_session(dom, require_session=True) as page:
            balance = get_points(page, dom)
    except SessionError as e:
        if as_json:
            print(json.dumps({"error": "session", "detail": str(e)}))
        else:
            console.print(f"[bold red]{e}[/bold red]")
        raise typer.Exit(EXIT_SESSION)
    if as_json:
        print(json.dumps(balance or {}, indent=2))
    else:
        if not balance:
            console.print("[bold red]Couldn't retrieve points balance.[/bold red]")
        else:
            for pf in balance.get("point_figures", []):
                console.print(f"Points figure: [bold]{pf}[/bold]")
            for df in balance.get("dollar_figures", []):
                console.print(f"Reward figure: [bold]${df}[/bold]")
            console.print("[dim]Open the My Points page in the window for the full "
                          "breakdown (2026 site no longer exposes a JSON API).[/dim]")
    raise typer.Exit(EXIT_OK)


@app.command()
def profile(
    domain: str = typer.Option(None, "--domain"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Account profile info."""
    dom = domain or _domain()
    try:
        with browser_session(dom, require_session=True) as page:
            data = get_profile(page, dom)
    except SessionError as e:
        if as_json:
            print(json.dumps({"error": "session", "detail": str(e)}))
        else:
            console.print(f"[bold red]{e}[/bold red]")
        raise typer.Exit(EXIT_SESSION)
    if as_json:
        print(json.dumps(data or {}, indent=2))
    else:
        if not data:
            console.print("[bold red]Couldn't retrieve profile.[/bold red]")
        else:
            if data.get("first_name"):
                console.print(f"[bold]{data['first_name']}[/bold]")
            console.print(
                "[dim]2026 site no longer exposes the profile JSON API — the "
                "dashboard page is open in the window.[/dim]"
            )
    raise typer.Exit(EXIT_OK)


@app.command()
def purchases(
    domain: str = typer.Option(None, "--domain"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Purchases summary by year."""
    dom = domain or _domain()
    try:
        with browser_session(dom, require_session=True) as page:
            data = get_purchases(page, dom)
    except SessionError as e:
        if as_json:
            print(json.dumps({"error": "session", "detail": str(e)}))
        else:
            console.print(f"[bold red]{e}[/bold red]")
        raise typer.Exit(EXIT_SESSION)
    if not data:
        if as_json:
            print(json.dumps({"error": "no-purchases"}))
        else:
            console.print("[bold red]Couldn't retrieve purchases.[/bold red]")
        raise typer.Exit(EXIT_FAIL)
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        console.print("[bold]My Purchases (latest page text):[/bold]")
        for line in data.get("page_text", "").splitlines()[:40]:
            if line.strip():
                console.print(line)
    raise typer.Exit(EXIT_OK)


@app.command()
def discover(
    domain: str = typer.Option(None, "--domain"),
    out: Path = typer.Option(KROGER_STATE_DIR / "discover", "--out", help="Output prefix"),
) -> None:
    """Dump the live coupons page for selector tuning (one-time)."""
    dom = domain or _domain()
    try:
        with browser_session(dom, require_session=True) as page:
            discover_dump(page, dom, out)
    except SessionError as e:
        console.print(f"[bold red]{e}[/bold red]")
        raise typer.Exit(EXIT_SESSION)
    console.print(f"[green]Wrote {out}.json (API dump) and {out}.meta.json[/green]")
    raise typer.Exit(EXIT_OK)


if __name__ == "__main__":
    app()
