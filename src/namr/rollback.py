"""Restore activities to their previous title given a date range."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from . import db
from .config import Settings
from .strava import StravaClient

log = logging.getLogger(__name__)


def run_rollback(
    settings: Settings,
    *,
    since: datetime,
    until: Optional[datetime] = None,
    dry_run: bool = True,
    per_request_sleep_s: float = 1.0,
) -> dict:
    db.init_db(settings.db_path)
    since_iso = since.astimezone(timezone.utc).isoformat(timespec="seconds")
    until_iso = (until or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
    rows = db.rename_log_in_range(settings.db_path, since_iso, until_iso)

    counts: dict[str, int] = {"restored": 0, "dry_run": 0, "error": 0}
    if not rows:
        return counts

    with StravaClient(
        client_id=settings.strava_client_id,
        client_secret=settings.strava_client_secret,
        db_path=settings.db_path,
    ) as client:
        # Restore in reverse chronological order so each activity ends up with
        # the *oldest* original title we have on file for it.
        seen: set[int] = set()
        for row in sorted(rows, key=lambda r: r["written_at"], reverse=True):
            aid = int(row["activity_id"])
            if aid in seen:
                continue
            seen.add(aid)
            original = row["original_title"]
            log_ctx = {"activity_id": aid, "restore_to": original}
            if dry_run:
                log.info("rollback_dry_run", extra=log_ctx)
                counts["dry_run"] += 1
                continue
            try:
                client.update_activity_name(aid, original)
                log.info("rolled_back", extra=log_ctx)
                counts["restored"] += 1
            except Exception:
                log.exception("rollback_failed", extra=log_ctx)
                counts["error"] += 1
            time.sleep(per_request_sleep_s)
    return counts
