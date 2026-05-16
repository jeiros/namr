# namr — not another morning run

A single-user service that rewrites Strava activity titles from defaults
(`Morning Run`, `Lunch Ride`, `Carrera matinal`, `Cursa al matí`…) to
short, original, context-aware titles using Claude.

**The rule it never breaks:** if a title isn't a known default pattern, it
is not touched. Manual titles — past or future — are always preserved.

## Architecture (TL;DR)

- **Trigger**: polling every 5 min (`/athlete/activities` with `after=`).
  No public endpoint, no webhook handshake, no inbound HTTPS — chosen for
  operational simplicity on a single-user deployment.
- **Storage**: SQLite at `data/namr.db` — OAuth tokens, processed-activity
  set (idempotency), title log (rollback + anti-repetition), daily LLM
  usage counter (budget cap).
- **Title generation**: Anthropic Messages API with the system prompt
  cached. Validate-and-retry loop enforces length / emoji / blocklist /
  no-duplicate constraints.
- **Context**: each activity is enriched with reverse-geocoded place
  (Nominatim) and a weather snapshot (Open-Meteo) before generation, both
  best-effort.

## Setup

```bash
# 1. install (uv)
uv venv
uv pip install -e ".[dev]"

# 2. configure
cp .env.example .env
# fill in STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, ANTHROPIC_API_KEY
# (create the Strava app at https://www.strava.com/settings/api,
#  use http://localhost:8721/callback as the Authorization Callback Domain
#  → set Authorization Callback Domain to: localhost)

# 3. one-time authorization (opens your browser)
uv run namr authorize

# 4. verify
uv run namr whoami
```

## Run

```bash
# foreground (Ctrl-C to stop)
uv run namr run

# single iteration for testing
uv run namr poll

# rename a specific activity (handy for debugging)
uv run namr process 1234567890 --force
```

## Disable (kill switches)

Three ways to stop renaming without redeploying:

- `NAMR_DISABLED=true` → service runs, polls, processes nothing
- `NAMR_DRY_RUN=true`  → service generates titles and logs them but does
  not write to Strava (useful for tuning the prompt)
- `NAMR_DAILY_LLM_CAP=0` → blocks generation past 0 calls/day

## Backfill (historical activities)

```bash
# dry-run: show what would be renamed (default)
uv run namr backfill --since 2025-01-01

# write
uv run namr backfill --since 2025-01-01 --write
```

Backfill sleeps between requests to respect Strava's 100 req / 15 min cap.

## Rollback

Every rename is logged with its original title. To restore:

```bash
uv run namr rollback --since 2026-05-15        # dry-run
uv run namr rollback --since 2026-05-15 --write
```

If an activity has multiple rename log entries, the *oldest* original
title is restored (i.e. as close to the user's original as we have).

## Observability

- Logs are JSON lines on stdout. One line per processed activity with
  `activity_id`, `original_title`, `new_title`, `latency_ms`, `attempts`,
  `outcome`.
- `uv run namr recent --limit 20` shows the last N rewrites from the
  local log.
- Daily LLM call/token counts live in the `llm_usage` table.

## Deploying to a Linux box (DigitalOcean / any VM)

```bash
# on the server, as a non-root user:
sudo mkdir -p /opt/namr && sudo chown $USER /opt/namr
git clone <this repo> /opt/namr
cd /opt/namr
uv venv
uv pip install -e .
cp .env.example .env && $EDITOR .env
uv run namr authorize     # do this from a session where you can open a browser
# (the auth flow listens on localhost:8721 — if you're on a headless box,
#  run `namr authorize` locally then copy data/namr.db to the server)

sudo cp deploy/namr.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now namr
sudo journalctl -u namr -f
```

## Configuration reference

See `.env.example` for the full list. Key knobs:

| var | default | meaning |
|---|---|---|
| `NAMR_MODEL` | `claude-sonnet-4-6` | generation model |
| `NAMR_POLL_INTERVAL_SECONDS` | `300` | seconds between polls |
| `NAMR_LOOKBACK_HOURS` | `24` | how far back each poll looks |
| `NAMR_PROCESS_DELAY_SECONDS` | `90` | wait this long after upload before processing — lets Strava settle metadata |
| `NAMR_SPORTS` | `Run,Ride,TrailRun,VirtualRun,VirtualRide` | sport allowlist; empty = all |
| `NAMR_SKIP_COMMUTE` | `true` | skip activities tagged `commute` |
| `NAMR_SKIP_RACE` | `true` | skip activities with `workout_type` ∈ {1, 11} |
| `NAMR_DAILY_LLM_CAP` | `50` | hard cap on generations per UTC day |
| `NAMR_USE_GEOCODE` | `true` | enrich with Nominatim |
| `NAMR_USE_WEATHER` | `true` | enrich with Open-Meteo |

## What it doesn't do (v1)

- No multi-tenancy.
- No web UI.
- No description rewrites (titles only).
- No photo or social-graph context.
- No custom sport-specific generation rules beyond what the prompt naturally produces.

## References

- Strava API: <https://developers.strava.com/docs/reference/>
- Strava OAuth: <https://developers.strava.com/docs/authentication/>
- Open-Meteo: <https://open-meteo.com/en/docs>
- Nominatim: <https://nominatim.org/release-docs/develop/api/Reverse/>
- Anthropic API: <https://docs.claude.com/en/api/getting-started>
