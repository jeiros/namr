"""Context enrichers: reverse-geocode start coords (Nominatim) and fetch a
weather snapshot for the activity start (Open-Meteo).

Both are best-effort: a failure here logs a warning and returns None so the
pipeline can still generate a title from intrinsic activity data.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
_OPEN_METEO = "https://archive-api.open-meteo.com/v1/archive"
_OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"


def reverse_geocode(lat: float, lon: float, *, user_agent: str) -> Optional[dict]:
    """Return a small dict with the most useful place fields, or None."""
    try:
        with httpx.Client(timeout=10, headers={"User-Agent": user_agent}) as c:
            r = c.get(
                _NOMINATIM,
                params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 14},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.warning("reverse_geocode_failed", extra={"error": str(e)})
        return None
    addr = data.get("address", {})
    place = (
        addr.get("neighbourhood")
        or addr.get("suburb")
        or addr.get("village")
        or addr.get("town")
        or addr.get("city")
        or addr.get("municipality")
    )
    return {
        "place": place,
        "city": addr.get("city") or addr.get("town") or addr.get("village"),
        "region": addr.get("state") or addr.get("region"),
        "country": addr.get("country"),
        "country_code": addr.get("country_code"),
        "display": data.get("display_name"),
    }


def _weather_code_label(code: int) -> str:
    # https://open-meteo.com/en/docs (WMO weather code)
    table = {
        0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
        45: "fog", 48: "rime fog",
        51: "drizzle", 53: "drizzle", 55: "drizzle",
        56: "freezing drizzle", 57: "freezing drizzle",
        61: "light rain", 63: "rain", 65: "heavy rain",
        66: "freezing rain", 67: "freezing rain",
        71: "light snow", 73: "snow", 75: "heavy snow",
        77: "snow grains",
        80: "rain showers", 81: "rain showers", 82: "heavy rain showers",
        85: "snow showers", 86: "snow showers",
        95: "thunderstorm", 96: "thunderstorm w/ hail", 99: "thunderstorm w/ hail",
    }
    return table.get(code, f"code-{code}")


def fetch_weather(lat: float, lon: float, start_iso: str) -> Optional[dict]:
    """Hourly snapshot near `start_iso`. Uses archive API if old enough,
    forecast API otherwise."""
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    except Exception:
        return None
    now = datetime.now(timezone.utc)
    use_archive = start < (now - timedelta(days=2))
    base = _OPEN_METEO if use_archive else _OPEN_METEO_FORECAST
    day = start.date().isoformat()
    try:
        with httpx.Client(timeout=10) as c:
            params = {
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m,precipitation,wind_speed_10m,weather_code",
                "timezone": "UTC",
                "start_date": day,
                "end_date": day,
            }
            r = c.get(base, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.warning("weather_fetch_failed", extra={"error": str(e)})
        return None

    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return None
    target_hour = start.replace(minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:00"
    )
    try:
        i = times.index(target_hour)
    except ValueError:
        i = min(range(len(times)), key=lambda k: abs(k - start.hour))
    def _g(key: str) -> Optional[float]:
        arr = hourly.get(key) or []
        return arr[i] if i < len(arr) else None
    code = _g("weather_code")
    return {
        "temp_c": _g("temperature_2m"),
        "precip_mm": _g("precipitation"),
        "wind_kmh": _g("wind_speed_10m"),
        "condition": _weather_code_label(int(code)) if code is not None else None,
    }
