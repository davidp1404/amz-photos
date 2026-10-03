"""Command-line interface: login, sync, views, status, repair (design D9).

``login`` is the only interactive command. ``sync`` never prompts: with no
stored session it fails fast with a re-authentication instruction and a non-zero
exit, so it is safe to run from a timer.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer
from rich.console import Console

from . import auth
from .client import AmazonPhotosClient, determine_tld
from .errors import AmzError, NoCookiesFoundError, SessionExpiredError
from .state import StateStore, default_state_path, status_report
from .sync import RepairResult, SyncSummary, rebuild_state
from .sync import sync as run_sync
from .tree import build_remote_tree
from .views import generate_views

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_AUTH = 2
EXIT_ERROR = 3

app = typer.Typer(
    help="Incremental one-way backup from Amazon Photos to a local library.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)

DestOption = typer.Option(Path("amz-library"), "--dest", "-d", help="Destination root.")
CredsOption = typer.Option(
    None, "--credentials", help="Path to the stored session file."
)


def build_client(session: dict[str, str], concurrency: int) -> AmazonPhotosClient:
    """Construct a client; overridable in tests."""
    return AmazonPhotosClient(session, concurrency=concurrency)


def print_summary(summary: SyncSummary, *, console_: Console | None = None) -> None:
    out = console_ or console
    label = "Dry run" if summary.dry_run else "Sync summary"
    out.print(f"[bold]{label}[/bold]")
    out.print(
        "listed={listed} downloaded={downloaded} refreshed={refreshed} "
        "moved={moved} healed={healed} skipped={skipped} deferred={deferred} "
        "archived={archived} failed={failed} bytes={bytes}".format(
            listed=summary.listed,
            downloaded=summary.downloaded,
            refreshed=summary.refreshed,
            moved=summary.moved,
            healed=summary.healed,
            skipped=summary.skipped,
            deferred=summary.deferred,
            archived=summary.archived,
            failed=summary.failed,
            bytes=summary.bytes,
        )
    )
    if summary.views_skipped and summary.views_warning:
        out.print(f"[yellow]{summary.views_warning}[/yellow]")
    for message in summary.errors:
        out.print(f"[red]{message}[/red]")


def _require_session(credentials: Path | None) -> dict[str, str]:
    session = auth.load_session(credentials)
    if session is None:
        err_console.print(
            "[red]No stored session.[/red] "
            "Re-authenticate with `amz-download login` before syncing."
        )
        raise typer.Exit(EXIT_AUTH)
    return session


# --- commands ----------------------------------------------------------------


@app.command()
def version() -> None:
    """Print the installed version."""
    from . import __version__

    typer.echo(__version__)


@app.command()
def login(
    cookie_file: Path = typer.Option(
        None, "--cookie-file", help="Netscape/name=value/JSON cookie file."
    ),
    firefox: bool = typer.Option(
        False, "--firefox", help="Read cookies from a local Firefox profile."
    ),
    profile: Path = typer.Option(
        None, "--profile", help="Explicit Firefox profile directory or cookies.sqlite."
    ),
    credentials: Path = CredsOption,
) -> None:
    """Capture an Amazon Photos session (interactive)."""
    try:
        if cookie_file is not None:
            cookies = auth.read_cookie_file(cookie_file)
        else:
            cookies = auth.extract_firefox_cookies(profile)
    except NoCookiesFoundError as exc:
        if cookie_file is None and sys.stdin.isatty():
            cookie_file = Path(typer.prompt("Path to cookie file")).expanduser()
            cookies = auth.read_cookie_file(cookie_file)
        else:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(EXIT_AUTH)
    try:
        auth.validate_cookies(cookies)
    except auth.MissingCookieError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(EXIT_AUTH)

    target = auth.save_session(cookies, credentials)
    console.print(f"Session stored at {target}")


@app.command()
def check(credentials: Path = CredsOption) -> None:
    """Validate the stored session with a single request (no writes).

    Safe to call from timers and health checks: exit 0 when the session is
    accepted, 2 when it is missing or rejected, 3 on a network/other error.
    """
    session = _require_session(credentials)
    region = f"amazon.{determine_tld(session)}"

    async def run() -> None:
        client = build_client(session, 1)
        try:
            await client.check_session()
        finally:
            await client.aclose()

    try:
        asyncio.run(run())
    except SessionExpiredError as exc:
        err_console.print(f"[red]session invalid:[/red] {exc}")
        raise typer.Exit(EXIT_AUTH)
    except AmzError as exc:
        err_console.print(f"[red]session check failed:[/red] {exc}")
        raise typer.Exit(EXIT_ERROR)
    console.print(f"session valid (region: {region})")


@app.command()
def sync(
    dest: Path = DestOption,
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report only; write nothing."
    ),
    concurrency: int = typer.Option(4, "--concurrency", "-c", min=1),
    limit: int = typer.Option(
        None, "--limit", min=1, help="Maximum number of content transfers."
    ),
    views: bool = typer.Option(
        True, "--views/--no-views", help="Regenerate views after the run."
    ),
    credentials: Path = CredsOption,
) -> None:
    """Synchronize the local library with Amazon Photos (non-interactive)."""
    session = _require_session(credentials)
    store = StateStore(default_state_path(dest))

    async def run() -> SyncSummary:
        client = build_client(session, concurrency)
        try:
            return await run_sync(
                client,
                store,
                dest,
                dry_run=dry_run,
                limit=limit,
                generate_views_after=views,
                warn=lambda message: err_console.print(f"[yellow]{message}[/yellow]"),
            )
        finally:
            await client.aclose()

    try:
        summary = asyncio.run(run())
    except SessionExpiredError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(EXIT_AUTH)
    except AmzError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(EXIT_ERROR)
    print_summary(summary)
    store.close()
    if not summary.ok:
        raise typer.Exit(EXIT_FAILURES)


@app.command()
def views(
    dest: Path = DestOption,
    credentials: Path = CredsOption,
) -> None:
    """Regenerate the symlink views from recorded state (no network)."""
    store = StateStore(default_state_path(dest))
    if not store.path.exists():
        err_console.print(
            "[yellow]No recorded state; run `amz-download sync` first.[/yellow]"
        )
        raise typer.Exit(EXIT_ERROR)
    result = generate_views(
        store,
        dest,
        warn=lambda message: err_console.print(f"[yellow]{message}[/yellow]"),
    )
    store.close()
    console.print(
        f"views: links={result.links} created={result.created} "
        f"updated={result.updated} removed={result.removed}"
    )
    if result.skipped:
        console.print("[yellow]Views were skipped (symlinks unavailable).[/yellow]")


@app.command()
def status(
    dest: Path = DestOption,
    credentials: Path = CredsOption,
) -> None:
    """Report recorded library status (no network)."""
    store = StateStore(default_state_path(dest))
    report = status_report(store)
    store.close()
    console.print(
        f"present={report['present']} archived={report['archived']} albums={report['albums']}"
    )
    run = report["last_run"]
    if run is None:
        console.print("last_run: none")
    else:
        console.print(
            "last_run: downloaded={d} skipped={s} failed={f} at={at}".format(
                d=run.downloaded, s=run.skipped, f=run.failed, at=run.started_at
            )
        )
    console.print(f"failures={report['failures']}")


@app.command()
def repair(
    dest: Path = DestOption,
    concurrency: int = typer.Option(4, "--concurrency", "-c", min=1),
    credentials: Path = CredsOption,
) -> None:
    """Rebuild recorded state from the existing local library and a remote listing."""
    session = _require_session(credentials)
    store = StateStore(default_state_path(dest))
    store.initialize()

    async def run() -> RepairResult:
        client = build_client(session, concurrency)
        try:
            await client.check_session()
            media = await client.list_media()
            folders = await client.list_folders()
            tree = build_remote_tree(media, folders)
            return rebuild_state(store, media, tree, dest)
        finally:
            await client.aclose()

    try:
        result = asyncio.run(run())
    except SessionExpiredError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(EXIT_AUTH)
    except AmzError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(EXIT_ERROR)
    store.close()
    console.print(f"adopted={result.adopted} unmatched={len(result.unmatched)}")
    for path in result.unmatched:
        console.print(f"[yellow]unmatched: {path}[/yellow]")


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
