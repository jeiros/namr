"""Detect whether a Strava activity title is a default-generated one.

Hard rule (from the plan): if `is_default_title` returns False, never touch.
Be conservative — under-detect rather than over-detect.

Strava defaults are of the form "<time-of-day> <sport-noun>" in the athlete's
locale. We cover EN (baseline) and common ES / CA variants Strava ships.
"""
from __future__ import annotations

import re
import unicodedata

# English time-of-day words used by Strava.
_EN_TIMES = ["Morning", "Lunch", "Afternoon", "Evening", "Night"]

# English sport nouns Strava uses in default titles.
_EN_SPORTS = [
    "Run", "Trail Run", "Treadmill Run", "Virtual Run",
    "Ride", "Mountain Bike Ride", "Gravel Ride", "E-Bike Ride",
    "E-Mountain Bike Ride", "Virtual Ride", "Handcycle", "Velomobile",
    "Swim", "Open Water Swim", "Pool Swim",
    "Walk", "Hike",
    "Workout", "Weight Training", "Crossfit", "Yoga",
    "Activity",
    "Ski", "Snowboard", "Backcountry Ski", "Alpine Ski",
    "Ice Skate", "Roller Ski", "Inline Skate",
    "Kayak", "Canoe", "Row", "Stand Up Paddle", "Sail", "Surf",
    "Windsurf", "Kitesurf",
    "Rock Climb",
    "Soccer",
]

# English defaults that Strava emits as the sport name alone, with no
# time-of-day prefix (pool swims, for example, are titled "Pool Swim").
_EN_STANDALONE = [
    "Pool Swim",
]


def _norm(s: str) -> str:
    # case-fold + collapse whitespace + strip accents for robust matching
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s).strip().casefold()
    return s


def _english_defaults() -> set[str]:
    combos = {_norm(f"{t} {s}") for t in _EN_TIMES for s in _EN_SPORTS}
    return combos | {_norm(s) for s in _EN_STANDALONE}


# Spanish defaults — patterns Strava localizes to. We list each known variant
# explicitly. Sports nouns vary by gender; titles include articles.
# Strava ships both an "adjective" form (matinal/vespertina/...) and a
# "prepositional" form (de la mañana / de la tarde / ...), and some locales
# drop the article ("de mañana"), so the prepositional patterns make `la `
# optional.
_ES_PATTERNS = [
    # Run
    r"^carrera (matinal|del mediod[ií]a|vespertina|nocturna)$",
    r"^carrera (de (la )?ma[ñn]ana|del mediod[ií]a|de (la )?tarde|de (la )?noche)$",
    # Ride
    r"^vuelta en bici (matinal|del mediod[ií]a|vespertina|nocturna)$",
    r"^vuelta en bici (de (la )?ma[ñn]ana|del mediod[ií]a|de (la )?tarde|de (la )?noche)$",
    r"^salida en bici (matinal|del mediod[ií]a|vespertina|nocturna)$",
    r"^salida en bici (de (la )?ma[ñn]ana|del mediod[ií]a|de (la )?tarde|de (la )?noche)$",
    # Walk / Hike — "paseo" (Iberian) and "caminata" (Latin American)
    r"^paseo (matinal|del mediod[ií]a|vespertino|nocturno)$",
    r"^paseo (de (la )?ma[ñn]ana|del mediod[ií]a|de (la )?tarde|de (la )?noche)$",
    r"^caminata (matinal|del mediod[ií]a|vespertina|nocturna)$",
    r"^caminata (de (la )?ma[ñn]ana|del mediod[ií]a|de (la )?tarde|de (la )?noche)$",
    r"^excursi[oó]n (matinal|del mediod[ií]a|vespertina|nocturna)$",
    r"^excursi[oó]n (de (la )?ma[ñn]ana|del mediod[ií]a|de (la )?tarde|de (la )?noche)$",
    # Swim
    r"^nataci[oó]n (matinal|del mediod[ií]a|vespertina|nocturna)$",
    r"^nataci[oó]n (de (la )?ma[ñn]ana|del mediod[ií]a|de (la )?tarde|de (la )?noche)$",
    # Workout / generic activity
    r"^entrenamiento (matinal|del mediod[ií]a|vespertino|nocturno)$",
    r"^entrenamiento (de (la )?ma[ñn]ana|del mediod[ií]a|de (la )?tarde|de (la )?noche)$",
    r"^actividad (matinal|del mediod[ií]a|vespertina|nocturna)$",
    r"^actividad (de (la )?ma[ñn]ana|del mediod[ií]a|de (la )?tarde|de (la )?noche)$",
]

# Catalan defaults
_CA_PATTERNS = [
    # Run
    r"^cursa (matinal|del migdia|vespertina|nocturna)$",
    r"^cursa (al mat[ií]|al migdia|a la tarda|a la nit)$",
    # Ride
    r"^sortida en bici (matinal|del migdia|vespertina|nocturna)$",
    r"^volta en bici (al mat[ií]|al migdia|a la tarda|a la nit)$",
    # Walk / Hike
    r"^passejada (matinal|del migdia|vespertina|nocturna)$",
    r"^excursi[oó] (matinal|del migdia|vespertina|nocturna)$",
    # Swim
    r"^nataci[oó] (matinal|del migdia|vespertina|nocturna)$",
    # Workout / generic activity
    r"^entrenament (matinal|del migdia|vespertina|nocturna)$",
    r"^activitat (matinal|del migdia|vespertina|nocturna)$",
]


_EN_SET = _english_defaults()
_ES_RES = [re.compile(p) for p in _ES_PATTERNS]
_CA_RES = [re.compile(p) for p in _CA_PATTERNS]


def is_default_title(name: str | None) -> bool:
    """Return True if `name` matches a known Strava default title pattern."""
    if not name:
        # Empty / None counts as "default-ish" — let the renamer fill it.
        return True
    n = _norm(name)
    if not n:
        return True
    if n in _EN_SET:
        return True
    for r in _ES_RES:
        if r.match(n):
            return True
    for r in _CA_RES:
        if r.match(n):
            return True
    return False
