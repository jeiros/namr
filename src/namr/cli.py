from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import click

from . import backfill as backfill_mod
from . import db, pipeline, rollback as rollback_mod
from .config import load_settings
from .logging_setup import configure_logging
from .oauth_cli import run_authorize
from .strava import StravaClient

log = logging.getLogger(__name__)


def _parse_when(s: str) -> datetime:
    # Accepts YYYY-MM-DD or full ISO; assumes UTC if no tz.
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise click.BadParameter(f"can't parse date '{s}'") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@click.group()
@click.option("--log-level", default="INFO", show_default=True)
@click.pass_context
def cli(ctx: click.Context, log_level: str) -> None:
    """namr — Strava activity auto-renamer."""
    configure_logging(log_level)
    ctx.ensure_object(dict)
    ctx.obj["settings"] = load_settings()


@cli.command()
@click.option("--no-browser", is_flag=True, help="Don't try to open a browser.")
@click.pass_context
def authorize(ctx: click.Context, no_browser: bool) -> None:
    """Run the one-time OAuth flow and persist tokens."""
    s = ctx.obj["settings"]
    if not s.strava_client_id or not s.strava_client_secret:
        raise click.ClickException("STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must be set")
    db.init_db(s.db_path)
    result = run_authorize(
        client_id=s.strava_client_id,
        client_secret=s.strava_client_secret,
        redirect_uri=s.strava_redirect_uri,
        db_path=s.db_path,
        open_browser=not no_browser,
    )
    click.echo(json.dumps(result, indent=2))


@cli.command()
@click.pass_context
def whoami(ctx: click.Context) -> None:
    """Verify stored tokens by hitting /athlete."""
    s = ctx.obj["settings"]
    db.init_db(s.db_path)
    with StravaClient(
        client_id=s.strava_client_id,
        client_secret=s.strava_client_secret,
        db_path=s.db_path,
    ) as c:
        r = c._request("GET", "/athlete")
        r.raise_for_status()
        click.echo(json.dumps(r.json(), indent=2))


@cli.command()
@click.pass_context
def poll(ctx: click.Context) -> None:
    """Run one polling iteration and exit."""
    s = ctx.obj["settings"]
    outcomes = pipeline.poll_once(s)
    click.echo(json.dumps(outcomes, indent=2))


@cli.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    """Run the polling loop forever."""
    s = ctx.obj["settings"]
    pipeline.run_forever(s)


@cli.command("process")
@click.argument("activity_id", type=int)
@click.option("--force", is_flag=True, help="Reprocess even if already processed.")
@click.pass_context
def process_cmd(ctx: click.Context, activity_id: int, force: bool) -> None:
    """Process a single activity by id."""
    s = ctx.obj["settings"]
    db.init_db(s.db_path)
    with StravaClient(
        client_id=s.strava_client_id,
        client_secret=s.strava_client_secret,
        db_path=s.db_path,
    ) as c:
        outcome = pipeline.process_activity(
            settings=s, client=c, activity_id=activity_id, force=force
        )
    click.echo(outcome)


@cli.command()
@click.option("--since", required=True, help="Inclusive lower bound, YYYY-MM-DD or ISO datetime.")
@click.option("--until", default=None, help="Optional upper bound.")
@click.option("--dry-run/--write", default=True, help="Dry-run by default.")
@click.pass_context
def backfill(ctx: click.Context, since: str, until: Optional[str], dry_run: bool) -> None:
    """Walk historical activities and rename default-titled ones."""
    s = ctx.obj["settings"]
    counts = backfill_mod.run_backfill(
        s,
        since=_parse_when(since),
        until=_parse_when(until) if until else None,
        dry_run=dry_run,
    )
    click.echo(json.dumps(counts, indent=2))


@cli.command()
@click.option("--since", required=True, help="Restore activities renamed at/after this date.")
@click.option("--until", default=None)
@click.option("--dry-run/--write", default=True)
@click.pass_context
def rollback(ctx: click.Context, since: str, until: Optional[str], dry_run: bool) -> None:
    """Restore previously-renamed activities to their original titles."""
    s = ctx.obj["settings"]
    counts = rollback_mod.run_rollback(
        s,
        since=_parse_when(since),
        until=_parse_when(until) if until else None,
        dry_run=dry_run,
    )
    click.echo(json.dumps(counts, indent=2))


@cli.command("recent")
@click.option("--limit", default=20, type=int)
@click.pass_context
def recent_cmd(ctx: click.Context, limit: int) -> None:
    """Show recent renames."""
    s = ctx.obj["settings"]
    db.init_db(s.db_path)
    rows = db.recent_titles(s.db_path, limit=limit)
    for r in rows:
        click.echo(r)


if __name__ == "__main__":  # pragma: no cover
    cli()
