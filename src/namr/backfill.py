"""One-off historical backfill. Walks recent activities and renames default-titled
ones. Supports dry-run. Always sleeps between requests to respect rate limits."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Iterator, Optional

from . import db
from .config import Settings
from .pipeline import process_activity
from .strava import StravaClient

log = logging.getLogger(__name__)


def _iter_activities(
    client: StravaClient,
    *,
    after_epoch: int,
    before_epoch: Optional[int] = None,
    page_size: int = 50,
    inter_request_sleep_s: float = 1.5,
) -> Iterator[dict]:
    page = 1
    while True:
        params: dict = {"after": after_epoch, "per_page": page_size, "page": page}
        if before_epoch is not None:
            params["before"] = before_epoch
        r = client._request("GET", "/athlete/activities", params=params)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            return
        for a in batch:
            yield a
        page += 1
        time.sleep(inter_request_sleep_s)


def run_backfill(
    settings: Settings,
    *,
    since: datetime,
    until: Optional[datetime] = None,
    dry_run: bool = True,
    per_activity_sleep_s: float = 1.0,
) -> dict:
    db.init_db(settings.db_path)
    after = int(since.replace(tzinfo=timezone.utc).timestamp())
    before = int(until.replace(tzinfo=timezone.utc).timestamp()) if until else None

    # We honor dry_run for the LLM write path by overriding the in-memory setting.
    effective = settings.model_copy(update={"namr_dry_run": dry_run})

    counts: dict[str, int] = {}
    with StravaClient(
        client_id=settings.strava_client_id,
        client_secret=settings.strava_client_secret,
        db_path=settings.db_path,
    ) as client:
        for raw in _iter_activities(client, after_epoch=after, before_epoch=before):
            aid = raw.get("id")
            if not aid:
                continue
            outcome = process_activity(
                settings=effective,
                client=client,
                activity_id=int(aid),
                raw=raw,
            )
            counts[outcome] = counts.get(outcome, 0) + 1
            time.sleep(per_activity_sleep_s)
    return counts
