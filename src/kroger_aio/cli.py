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
from rich.table import Table

from . import __version__
from .account import get_points, get_profile, get_purchases, summarize_purchases
from .browser import SessionError, browser_session, run_visible_login
from .config import KROGER_STATE_DIR
from .coupons import clip_all, clip_status
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
    """Sign in once. Opens a visible Chrome window; session is persisted."""
    dom = domain or _domain()
    username = os.environ.get("KROGER_EMAIL", "")
    password = os.environ.get("KROGER_PASSWORD", "")
    if not username:
        username = typer.prompt("Kroger username (email)")
    if not password:
        password = typer.prompt("Password", hide_input=True)

    ok = run_visible_login(dom, username, password, console=console)
    if as_json:
        print(json.dumps({"signed_in": ok}))
    raise typer.Exit(EXIT_OK if ok else EXIT_FAIL)


@app.command()
def clip(
    domain: str = typer.Option(None, "--domain", help="Store domain"),
    as_json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Clip every available coupon (skips already-clipped)."""
    dom = domain or _domain()
    try:
        with browser_session(dom, require_session=True) as page:
            result = clip_all(page, dom)
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
        if new:
            console.print(f"[bold green]Clipped {len(new)} new coupon(s):[/bold green]")
            for name in new[:50]:
                console.print(f"  [green]+[/green] {name}")
            if len(new) > 50:
                console.print(f"  [dim]…and {len(new) - 50} more[/dim]")
        else:
            console.print("[green]All available coupons already clipped. Nothing new.[/green]")
        console.print(
            f"[dim]Clipped this run: {result['clipped']} | "
            f"skipped: {result['skipped']} | failed: {result['failed']}[/dim]"
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
            f"Total coupons: [bold]{s['total']}[/bold] | "
            f"Clipped: [green]{s['clipped']}[/green] | "
            f"Still available: [yellow]{s['available_unclipped']}[/yellow]"
        )
        if s["unclipped_sample"]:
            console.print("[bold]Unclipped (first 25):[/bold]")
            for c in s["unclipped_sample"]:
                console.print(f"  • {c['name'] or c['id'][:60]} {c['price']}")
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
        print(json.dumps(balance or [], indent=2))
    else:
        if not balance:
            console.print("[bold red]Couldn't retrieve points balance.[/bold red]")
        else:
            for item in balance[1:] if isinstance(balance, list) else []:
                try:
                    name = item["programDisplayInfo"]["loyaltyProgramName"]
                    bal = item["programBalance"]["balanceDescription"]
                    console.print(f"{name}: [bold]{bal}[/bold]")
                except (KeyError, TypeError):
                    continue
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
            console.print(
                f"{data.get('firstName', '')} {data.get('lastName', '')}\n"
                f"{data.get('emailAddress', '')}\n"
                f"Loyalty card: {data.get('loyaltyCardNumber', '')}"
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
    summary = summarize_purchases(data)
    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        t = Table(title=f"Purchases ({summary.get('first_purchase', '')[:10]} – {summary.get('last_purchase', '')[:10]})")
        t.add_column("Year")
        t.add_column("Visits")
        t.add_column("Spent")
        t.add_column("Saved")
        for year, y in summary.get("years", {}).items():
            t.add_row(str(year), str(y["store_visits"]), f"${y['total']:.2f}", f"${y['total_savings']:.2f}")
        tt = summary.get("total", {})
        t.add_row("Total", str(tt.get("store_visits", 0)), f"${tt.get('total', 0):.2f}", f"${tt.get('total_savings', 0):.2f}")
        console.print(t)
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
    console.print(f"[green]Wrote {out}.html and {out}.buttons.json[/green]")
    raise typer.Exit(EXIT_OK)


if __name__ == "__main__":
    app()
