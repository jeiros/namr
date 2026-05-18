"""Polling pipeline: fetch recent activities, filter, generate, write, log."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

from . import db, enrich, generator
from .config import Settings
from .detector import is_default_title
from .strava import StravaClient

log = logging.getLogger(__name__)

# Substring used to detect a previously-appended footer (idempotency).
FOOTER_MARKER = "github.com/jeiros/namr"


def _compose_description(existing: Optional[str], footer: str) -> Optional[str]:
    """Return the new description, or None if no change is needed.

    - If the marker is already present, no change.
    - If existing is empty/None, the new description is just the footer.
    - Otherwise we append the footer separated by a blank line.
    """
    existing = (existing or "").rstrip()
    if FOOTER_MARKER in existing:
        return None
    if not existing:
        return footer
    return f"{existing}\n\n{footer}"


_WORKOUT_TYPE_LABEL = {
    # https://developers.strava.com/docs/reference/#api-models-WorkoutType
    0: "default_run",
    1: "race_run",
    2: "long_run",
    3: "workout_run",
    10: "default_ride",
    11: "race_ride",
    12: "workout_ride",
}


def _pace_min_per_km(distance_m: float, moving_s: float) -> Optional[float]:
    if not distance_m or not moving_s:
        return None
    km = distance_m / 1000.0
    if km <= 0:
        return None
    return (moving_s / 60.0) / km


def _fmt_pace(s_per_km: float) -> str:
    m, s = divmod(int(round(s_per_km)), 60)
    return f"{m}:{s:02d}/km"


def _summarize_laps(raw: dict, sport: Optional[str], max_laps: int = 10) -> Optional[str]:
    laps = raw.get("laps") or []
    if not laps or len(laps) < 2:
        return None
    is_pace_sport = (sport or "").endswith("Run") or sport in {"Walk", "Hike"}
    rows: list[str] = []
    for lap in laps[:max_laps]:
        d_km = (lap.get("distance") or 0) / 1000.0
        mt = lap.get("moving_time") or 0
        hr = lap.get("average_heartrate")
        bits = [f"{d_km:.2f} km"]
        if is_pace_sport and d_km > 0:
            bits.append(_fmt_pace(mt / d_km))
        else:
            avg_speed = lap.get("average_speed")
            if avg_speed:
                bits.append(f"{float(avg_speed) * 3.6:.1f} km/h")
            else:
                bits.append(f"{mt // 60}:{mt % 60:02d}")
        if hr:
            bits.append(f"{int(hr)} bpm")
        rows.append(f"  {lap.get('lap_index', '?')}: " + ", ".join(bits))
    header = f"laps ({len(laps)}"
    if len(laps) > max_laps:
        header += f", first {max_laps} shown"
    header += "):"
    return header + "\n" + "\n".join(rows)


def _summarize_segments(raw: dict, max_segments: int = 6) -> Optional[str]:
    efforts = raw.get("segment_efforts") or []
    if not efforts:
        return None

    def rank_key(e: dict) -> tuple:
        # Lower tuple sorts first. We want PRs first, then KOMs, then longer climbs.
        pr = e.get("pr_rank") or 99
        kom = e.get("kom_rank") or 99
        seg = e.get("segment") or {}
        # Negative distance so longer climbs come first within the same rank tier.
        return (pr, kom, -(seg.get("distance") or 0))

    picked = sorted(efforts, key=rank_key)[:max_segments]
    rows: list[str] = []
    for e in picked:
        seg = e.get("segment") or {}
        name = (seg.get("name") or "").strip()
        d_km = (seg.get("distance") or 0) / 1000.0
        grade = seg.get("average_grade")
        et = e.get("elapsed_time") or 0
        m, s = divmod(int(et), 60)
        tags = []
        if e.get("pr_rank"):
            tags.append(f"PR #{e['pr_rank']}")
        if e.get("kom_rank"):
            tags.append(f"KOM #{e['kom_rank']}")
        bits = [f"{d_km:.1f} km"]
        if grade is not None:
            bits.append(f"{grade:+.1f}%")
        bits.append(f"{m}:{s:02d}")
        suffix = f" ({', '.join(tags)})" if tags else ""
        rows.append(f"  {name} — {', '.join(bits)}{suffix}")
    header = f"segments ({len(efforts)}"
    if len(efforts) > max_segments:
        header += f", top {max_segments} shown by PR/KOM/distance"
    header += "):"
    return header + "\n" + "\n".join(rows)


def _project_activity(raw: dict, settings: Settings) -> dict:
    distance_m = float(raw.get("distance") or 0)
    moving_s = float(raw.get("moving_time") or 0)
    start_local = raw.get("start_date_local")  # ISO without tz info per Strava
    day_of_week = None
    if start_local:
        try:
            day_of_week = datetime.fromisoformat(start_local).strftime("%A")
        except Exception:
            pass

    workout_type = raw.get("workout_type")
    activity = {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "sport_type": raw.get("sport_type") or raw.get("type"),
        "type": raw.get("type"),
        "distance_km": distance_m / 1000.0 if distance_m else None,
        "moving_time_min": moving_s / 60.0 if moving_s else None,
        "elev_gain_m": raw.get("total_elevation_gain"),
        "avg_speed_kmh": (float(raw["average_speed"]) * 3.6) if raw.get("average_speed") else None,
        "avg_pace_min_per_km": _pace_min_per_km(distance_m, moving_s),
        "avg_hr": raw.get("average_heartrate"),
        "max_hr": raw.get("max_heartrate"),
        "suffer_score": raw.get("suffer_score"),
        "start_date": raw.get("start_date"),
        "start_local": start_local,
        "day_of_week": day_of_week,
        "workout_type": workout_type,
        "workout_type_label": _WORKOUT_TYPE_LABEL.get(workout_type) if workout_type is not None else None,
        "commute": bool(raw.get("commute")),
        "trainer": bool(raw.get("trainer")),
        "start_latlng": raw.get("start_latlng") or None,
        # Strava's athlete_count counts the activity owner + companions on a
        # group activity. 1 means solo; >1 means a group ride/run/etc.
        "athlete_count": raw.get("athlete_count"),
    }

    latlng = activity["start_latlng"]
    if latlng and isinstance(latlng, list) and len(latlng) == 2 and latlng[0] is not None:
        if settings.namr_use_geocode:
            activity["place"] = enrich.reverse_geocode(
                latlng[0], latlng[1], user_agent=settings.namr_geocode_user_agent
            )
        if settings.namr_use_weather and activity["start_date"]:
            activity["weather"] = enrich.fetch_weather(
                latlng[0], latlng[1], activity["start_date"]
            )

    # Laps and segment efforts are only on DetailedActivity, not summaries.
    activity["laps_summary"] = _summarize_laps(raw, activity["sport_type"])
    activity["segments_summary"] = _summarize_segments(raw)
    return activity


def _should_skip(activity: dict, settings: Settings) -> Optional[str]:
    sport = activity.get("sport_type") or activity.get("type")
    if settings.sports and sport not in settings.sports:
        return f"sport_filter:{sport}"
    if settings.namr_skip_commute and activity.get("commute"):
        return "commute"
    if settings.namr_skip_race and activity.get("workout_type") in (1, 11):
        return "race"
    if not is_default_title(activity.get("name")):
        return "manual_title"
    return None


def process_activity(
    *,
    settings: Settings,
    client: StravaClient,
    activity_id: int,
    raw: Optional[dict] = None,
    force: bool = False,
) -> str:
    """Process one activity end to end. Returns outcome label."""
    db_path = settings.db_path

    if not force and db.is_processed(db_path, activity_id):
        return "already_processed"

    if raw is None:
        raw = client.get_activity(activity_id)

    # Cheap skip-checks first, using whatever raw we have (summary is enough).
    summary_proj = _project_activity(raw, settings)
    log_ctx = {
        "activity_id": activity_id,
        "sport_type": summary_proj.get("sport_type"),
        "original_title": summary_proj.get("name"),
    }
    skip = _should_skip(summary_proj, settings)
    if skip:
        log.info("skip", extra={**log_ctx, "reason": skip})
        outcome = "skipped_manual" if skip == "manual_title" else "skipped_filter"
        db.mark_processed(db_path, activity_id, outcome, skip)
        return outcome

    if settings.namr_disabled:
        log.info("disabled_skip", extra=log_ctx)
        db.mark_processed(db_path, activity_id, "skipped_filter", "disabled")
        return "skipped_filter"

    used = db.get_today_usage(db_path)
    if used >= settings.namr_daily_llm_cap:
        log.warning("daily_cap_reached", extra={**log_ctx, "used": used, "cap": settings.namr_daily_llm_cap})
        return "cap_reached"

    # We're going to generate. Fetch the detailed activity once so we have laps,
    # segment_efforts, and description — and project against that.
    detail = raw if ("laps" in raw or "segment_efforts" in raw) else None
    if detail is None:
        try:
            detail = client.get_activity(activity_id)
        except Exception:
            log.exception("detail_fetch_failed", extra=log_ctx)
            detail = raw  # fall back to summary

    activity = _project_activity(detail, settings)
    recent = db.recent_titles(db_path, limit=30)

    try:
        gen = generator.generate_title(
            backend=settings.namr_backend,
            api_key=settings.anthropic_api_key,
            claude_cli_path=settings.namr_claude_cli_path,
            model=settings.namr_model,
            activity=activity,
            recent_titles=recent,
        )
    except Exception as e:
        # Don't mark processed — let the next poll retry.
        log.exception("generation_failed", extra=log_ctx)
        return f"error:{type(e).__name__}"

    db.bump_usage(db_path, input_tokens=gen.input_tokens, output_tokens=gen.output_tokens)

    original = activity.get("name") or ""
    log_ctx_w = {
        **log_ctx,
        "new_title": gen.title,
        "attempts": gen.attempts,
        "latency_ms": gen.latency_ms,
        "model": gen.model,
    }

    new_description: Optional[str] = None
    if settings.namr_append_footer:
        new_description = _compose_description(
            detail.get("description"), settings.namr_footer
        )
        log_ctx_w["description_change"] = (
            "skip" if new_description is None else "append"
        )

    if settings.namr_dry_run:
        log.info("dry_run_would_rename", extra=log_ctx_w)
        db.log_rename(
            db_path,
            activity_id=activity_id,
            original_title=original,
            new_title=gen.title,
            sport_type=activity.get("sport_type"),
            model=gen.model,
            latency_ms=gen.latency_ms,
        )
        db.mark_processed(db_path, activity_id, "dry_run", gen.title)
        return "dry_run"

    try:
        client.update_activity(
            activity_id,
            name=gen.title,
            description=new_description,
        )
    except Exception:
        # Don't mark processed — let the next poll retry the write.
        log.exception("write_failed", extra=log_ctx_w)
        return "error:write"

    db.log_rename(
        db_path,
        activity_id=activity_id,
        original_title=original,
        new_title=gen.title,
        sport_type=activity.get("sport_type"),
        model=gen.model,
        latency_ms=gen.latency_ms,
    )
    db.mark_processed(db_path, activity_id, "renamed", None)
    log.info("renamed", extra=log_ctx_w)
    return "renamed"


def poll_once(settings: Settings) -> dict:
    db.init_db(settings.db_path)
    after = int(time.time()) - settings.namr_lookback_hours * 3600
    now = int(time.time())
    counts: dict[str, int] = {}
    with StravaClient(
        client_id=settings.strava_client_id,
        client_secret=settings.strava_client_secret,
        db_path=settings.db_path,
    ) as client:
        activities = client.list_recent_activities(after_epoch=after, per_page=30)
        for raw in activities:
            aid = raw.get("id")
            if not aid:
                continue
            # Honor processing delay — let Strava settle metadata.
            try:
                start_iso = raw.get("start_date")
                if start_iso:
                    start_ts = datetime.fromisoformat(
                        start_iso.replace("Z", "+00:00")
                    ).timestamp()
                    elapsed = now - int(start_ts)
                    if elapsed < settings.namr_process_delay_seconds:
                        counts["too_recent"] = counts.get("too_recent", 0) + 1
                        continue
            except Exception:
                pass
            outcome = process_activity(
                settings=settings,
                client=client,
                activity_id=int(aid),
                raw=raw,
            )
            counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def run_forever(settings: Settings) -> None:
    log.info("namr_starting", extra={
        "poll_interval_s": settings.namr_poll_interval_seconds,
        "lookback_h": settings.namr_lookback_hours,
        "dry_run": settings.namr_dry_run,
        "disabled": settings.namr_disabled,
        "model": settings.namr_model,
    })
    while True:
        try:
            outcomes = poll_once(settings)
            log.info("poll_complete", extra={"outcomes": outcomes})
        except Exception:
            log.exception("poll_failed")
        time.sleep(settings.namr_poll_interval_seconds)
