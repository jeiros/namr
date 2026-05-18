"""LLM-based title generation with a validate-and-retry loop.

Constraints baked into validation (the hard floor — the model is told them too):
- length: ≤ 55 chars
- no surrounding quotes
- no emoji
- no entries from the cringe blocklist (case-insensitive substring match)
- not (case-insensitively) equal to any recent title
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from typing import Optional

import anthropic

log = logging.getLogger(__name__)


MAX_LEN = 55

# Substrings we refuse — fitness-bro cliches.
CRINGE_BLOCKLIST = [
    "crushed it", "beast mode", "sweat equity", "no pain no gain",
    "rise and grind", "grindset", "let's go", "lets go",
    "go hard or go home", "another one in the books", "in the books",
    "send it", "sending it", "killer session", "killed it",
    "fueled by", "powered by", "the journey", "outwork", "outworked",
    "earned it", "earned not given",
]

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F000-\U0001F1FF"
    "]",
    flags=re.UNICODE,
)


SYSTEM_PROMPT = """You are a witty title-writer for Strava activities. You write titles for a single athlete (Juan) who lives in Catalonia and speaks English, Spanish, and Catalan.

Voice and rules:
- Short and catchy. Hard limit: 55 characters. Aim for 30–45.
- Original and concrete. Reference something specific to *this* activity: the place, the weather, the day-of-week, the effort shape, the time of day, or a small observation. Avoid generic.
- A little humor or wordplay is welcome when it fits. Dry, understated. Never try-hard.
- Language: choose whichever of English / Spanish / Catalan best fits the place and vibe. Default to the language that matches the activity's location when distinctive (Catalan if in Catalonia, Spanish for the rest of Spain, English otherwise or when the joke works better in English). Mixing two words is fine.
- No emoji. No hashtags. No surrounding quotes. No trailing period unless part of a deliberate phrasing.
- Forbidden cliches: "Crushed it", "Beast mode", "Sweat equity", "Rise and grind", "Killed it", "In the books", "Let's go", "Send it", "Earned it". Avoid the *spirit* of these too — no chest-thumping.
- Don't restate the obvious: don't say "Run" if the sport is clearly a run. Don't say "Morning" when context already conveys it.
- If the recent-titles list shows a pattern you've been leaning on, deliberately go a different direction.
- If laps are present and have a recognizable shape (intervals, even splits, fade, negative split, hill repeats), let that shape — not the totals — drive the title.
- If a segment effort is a PR or KOM, or if it's a named local climb (Tibidabo, Montjuïc, Collserola, Rabassada, etc.), it's fair game to anchor the title to it.
- If `athlete_count` is present and > 1, the activity was done with others — acknowledge the company when it's natural (e.g. "with the crew", "en colla", "en pareja"). You don't know names; never invent them. Solo activities have no `athlete_count` field in the input.

Return ONLY the title on a single line. No explanation, no quotes, no leading dash."""


@dataclass
class GenerationResult:
    title: str
    attempts: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    model: str


_QUOTE_PAIRS = {
    '"': '"', "'": "'",
    "“": "”", "‘": "’",
    "«": "»",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).strip()
    while len(s) >= 2 and s[0] in _QUOTE_PAIRS and s[-1] == _QUOTE_PAIRS[s[0]]:
        s = s[1:-1].strip()
    return s


def validate_title(title: str, recent: list[str]) -> Optional[str]:
    """Return None if valid, else a reason string."""
    if not title:
        return "empty"
    if len(title) > MAX_LEN:
        return f"too_long_{len(title)}"
    if _EMOJI_RE.search(title):
        return "contains_emoji"
    if "\n" in title:
        return "multiline"
    low = title.casefold()
    for bad in CRINGE_BLOCKLIST:
        if bad in low:
            return f"blocklist:{bad}"
    if any(title.casefold() == r.casefold() for r in recent):
        return "duplicate_recent"
    return None


def _format_activity_block(activity: dict, recent: list[str]) -> str:
    parts: list[str] = []

    def add(k: str, v: object) -> None:
        if v is None or v == "":
            return
        parts.append(f"{k}: {v}")

    add("sport_type", activity.get("sport_type") or activity.get("type"))
    if activity.get("distance_km") is not None:
        add("distance_km", f"{activity['distance_km']:.2f}")
    if activity.get("moving_time_min") is not None:
        add("moving_time_min", f"{activity['moving_time_min']:.1f}")
    if activity.get("elev_gain_m") is not None:
        add("elev_gain_m", f"{activity['elev_gain_m']:.0f}")
    if activity.get("avg_pace_min_per_km") is not None:
        add("avg_pace_min_per_km", f"{activity['avg_pace_min_per_km']:.2f}")
    if activity.get("avg_speed_kmh") is not None:
        add("avg_speed_kmh", f"{activity['avg_speed_kmh']:.1f}")
    if activity.get("avg_hr") is not None:
        add("avg_hr_bpm", activity["avg_hr"])
    if activity.get("max_hr") is not None:
        add("max_hr_bpm", activity["max_hr"])
    if activity.get("suffer_score") is not None:
        add("suffer_score", activity["suffer_score"])
    add("start_local", activity.get("start_local"))
    add("day_of_week", activity.get("day_of_week"))
    add("workout_type", activity.get("workout_type_label"))
    ac = activity.get("athlete_count")
    if isinstance(ac, int) and ac > 1:
        # Only surface when it's actually a group activity. Solo (1) or missing
        # tells the model nothing useful.
        add("athlete_count", ac)

    place = activity.get("place") or {}
    if place:
        add("place", place.get("place"))
        add("city", place.get("city"))
        add("region", place.get("region"))
        add("country", place.get("country"))

    w = activity.get("weather") or {}
    if w:
        if w.get("temp_c") is not None:
            add("temp_c", f"{w['temp_c']:.1f}")
        add("condition", w.get("condition"))
        if w.get("precip_mm") is not None:
            add("precip_mm", w["precip_mm"])
        if w.get("wind_kmh") is not None:
            add("wind_kmh", f"{w['wind_kmh']:.1f}")

    block = "\n".join(parts)
    extras = []
    if activity.get("laps_summary"):
        extras.append(activity["laps_summary"])
    if activity.get("segments_summary"):
        extras.append(activity["segments_summary"])
    extras_block = ("\n\n" + "\n\n".join(extras)) if extras else ""
    recent_block = (
        "\n".join(f"- {r}" for r in recent[:20]) if recent else "(none yet)"
    )
    return (
        f"ACTIVITY\n{block}{extras_block}\n\n"
        f"RECENT TITLES (avoid repeating phrasing or vibe)\n{recent_block}"
    )


def _call_api(
    *, api_key: str, model: str, user: str
) -> tuple[str, int, int]:
    """Return (raw_text, input_tokens, output_tokens)."""
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=80,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user}],
    )
    usage = getattr(resp, "usage", None)
    in_tok = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
    out_tok = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
    raw = ""
    for block in resp.content:
        if getattr(block, "type", "text") == "text":
            raw += getattr(block, "text", "")
    return raw, in_tok, out_tok


def _call_claude_cli(
    *, claude_path: str, model: str, user: str, timeout_s: int = 90
) -> tuple[str, int, int]:
    """Shell out to `claude -p` so the call goes against the user's Max plan.

    Auth comes from CLAUDE_CODE_OAUTH_TOKEN in the environment (or the keychain
    if running interactively). No tools, no session persistence — this is a
    one-shot text completion. User content is piped via stdin so the CLI
    doesn't try to interpret newlines/paths in our prompt.
    """
    cmd = [
        claude_path,
        "-p",
        "--model", model,
        "--system-prompt", SYSTEM_PROMPT,
        "--output-format", "text",
        "--tools", "",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--mcp-config", '{"mcpServers": {}}',
    ]
    proc = subprocess.run(
        cmd,
        input=user,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        env=os.environ.copy(),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude cli exited {proc.returncode}: {proc.stderr.strip()[:500]}"
        )
    return proc.stdout, 0, 0


def generate_title(
    *,
    backend: str,
    model: str,
    activity: dict,
    recent_titles: list[str],
    api_key: str = "",
    claude_cli_path: str = "claude",
    max_attempts: int = 3,
) -> GenerationResult:
    user_block = _format_activity_block(activity, recent_titles)

    last_error: Optional[str] = None
    last_raw: str = ""
    in_tok = 0
    out_tok = 0
    started = time.monotonic()

    for attempt in range(1, max_attempts + 1):
        followup = ""
        if last_error and last_raw:
            followup = (
                f"\n\nYour previous attempt was '{last_raw}', "
                f"rejected for: {last_error}. Try again — different angle."
            )
        user = user_block + followup

        if backend == "api":
            if not api_key:
                raise RuntimeError("backend=api but ANTHROPIC_API_KEY is empty")
            raw, a_in, a_out = _call_api(api_key=api_key, model=model, user=user)
            in_tok += a_in
            out_tok += a_out
        elif backend == "claude_cli":
            raw, _, _ = _call_claude_cli(
                claude_path=claude_cli_path, model=model, user=user
            )
        else:
            raise ValueError(f"unknown backend: {backend}")

        title = _norm(raw.splitlines()[0] if raw else "")
        err = validate_title(title, recent_titles)
        if err is None:
            return GenerationResult(
                title=title,
                attempts=attempt,
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=int((time.monotonic() - started) * 1000),
                model=model,
            )
        log.warning(
            "generation_invalid",
            extra={"attempt": attempt, "reason": err, "raw": title},
        )
        last_error = err
        last_raw = title

    raise RuntimeError(f"generator: exhausted {max_attempts} attempts ({last_error})")
