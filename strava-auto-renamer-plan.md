# Strava Activity Auto-Renamer — Implementation Plan

Repo name is "namr" - not-another-morning-run

## Goal

Build a service that automatically rewrites Strava activity titles from default patterns (e.g. "Morning Run", "Evening Workout", "Lunch Ride") to original, context-aware titles, while strictly respecting any titles the user has set manually.

## Success Criteria

- New activities with default-pattern titles get renamed within minutes of upload.
- Titles edited manually (now or in future) are **never** overwritten.
- Generated titles feel original — not LLM-cliché — stay under Strava's length limit, and meaningfully reflect activity context (location, effort, conditions, time of day). Humorous if possible. Short and catchy is a must.
- Service runs unattended for at least a week with no intervention.
- Kill-switch and rollback are trivial.

## Constraints

- **Strava API**: OAuth2 with `activity:write` scope, access token TTL ~6h, rate limit 100 req / 15 min and 1000 req / day.
- **Strava webhooks**: require public HTTPS endpoint with verification handshake; delivery is at-least-once; handlers must be idempotent.
- Activity metadata may be incomplete on first webhook event (description, laps, segments populate later via subsequent `update` events).
- LLM cost ceiling: define a monthly budget; throttle if exceeded.
- Single-user scope (Juan's account only) — no multi-tenancy concerns.

## Architecture Decisions (Claude Code to propose with justification)

These are deliberately left open. Pick one, document the tradeoff in the README:

- **Trigger mechanism**: webhook (lower latency, requires public endpoint) vs scheduled polling (simpler, no inbound HTTP). For single-user, polling every 5–10 min is often plenty.
- **Hosting target**: DigitalOcean VM (alongside existing OpenClaw infra), Cloudflare Worker, AWS Lambda, or a small VPS. Pick based on chosen trigger and existing operational comfort.
- **Title generation strategy**: pure LLM, template + LLM polish, or theme rotation + LLM. Whichever, document the prompt.
- **Token storage**: encrypted at rest in a secret store appropriate to the host (e.g. CF Workers Secrets, dotenv with restricted perms, KMS).
- **State storage**: minimum is OAuth tokens + a recent-titles log (for anti-repetition) + a processed-activity-ids set (for idempotency).

## Phases

### Phase 0 — Strava OAuth & token plumbing
- Register Strava API application, obtain `client_id` / `client_secret`.
- Implement OAuth authorization-code flow end to end.
- Persist tokens; implement automatic refresh on 401.
- One-time CLI/script to perform initial authorization.

### Phase 1 — Activity read & default-title detection
- Fetch activity by ID via Strava API.
- Implement a predicate `is_default_title(name, type)` matching Strava's default patterns across likely locales (English baseline; check Spanish/Catalan based on account locale).
- Hard rule: if `is_default_title` returns false, skip — never touch.

### Phase 2 — Title generation
- Define the structured input contract passed to the generator: sport type, distance, duration, elevation gain, HR/pace summary, start lat/lng + reverse-geocoded place, start time/day-of-week, weather snapshot (optional), and a window of recent titles to avoid repetition.
- Define the prompt; bake constraints in:
  - ≤55 characters
  - no emoji, no surrounding quotes
  - no cringe ("Crushed it", "Beast mode", "Sweat equity", etc.) — maintain an explicit blocklist
  - bilingual flavor (English / Spanish / Catalan) acceptable and welcome
- Add a validate-and-retry loop: regenerate up to N times if output violates constraints.

### Phase 3 — Activity update (write path)
- PUT to `/api/v3/activities/{id}` with new `name`.
- Log original → new title for every write.
- Handle 401 (refresh + retry once), 429 (exponential backoff), 5xx (retry with jitter).

### Phase 4 — Trigger mechanism
- **Webhook path**: subscribe via API, expose verification GET, handle event POSTs, filter to `aspect_type=create` + `object_type=activity`, add 60–90s processing delay so metadata settles.
- **Polling path**: scheduled job fetches recent activities, processes those not yet seen.
- Either way: maintain a processed-activity-ids set to dedupe.

### Phase 5 — Deployment & observability
- Structured logging: one line per processed activity with id, original title, new title, generation latency, LLM cost, outcome.
- Alerting on repeated failures (auth, rate limit, generation errors).
- Health endpoint for webhook receiver (or a heartbeat log line for the poller).
- **Kill switch**: env var or feature flag that disables writes globally without redeploy.

### Phase 6 — Backfill (optional, post-MVP)
- One-off script: walk historical activities, identify default-titled ones, rename them.
- Built-in rate-limit awareness (sleep between requests).
- Dry-run mode that logs what *would* be renamed without writing.

## Non-Functional Requirements

- **Idempotent**: receiving the same webhook event twice must not double-rename.
- **Observable**: a single log view that answers "what got renamed in the last 24h, what failed, why".
- **Recoverable**: if the service dies for hours, missed activities should be processable via a reconciliation pass or the next poll.
- **Reversible**: original titles stored in a local log; a rollback script should be able to restore any activity to its previous title given a date range.

## Open Questions for Juan

These need answers before or during implementation:

1. **Hosting target** — DigitalOcean (existing OpenClaw infra), Cloudflare Workers, or somewhere else?
2. **LLM provider** — Claude API (default assumption), or a local/alternative model?
3. **Tone/personality** — lean into multilingual (Catalan/Spanish) flavor, or stay English-only?
4. **Scope of rewrite** — title only, or also description?
5. **Sport filter** — rename everything, or only runs and rides? Skip swims, strength, workouts?
6. **Exclusions** — should activities tagged `commute` or `race` be skipped automatically?
7. **Budget cap** — monthly LLM spend ceiling?

## Out of Scope (v1)

- Multi-user support.
- Any web UI; CLI + logs only.
- Renaming based on social context (kudos, mentions, segments).
- Photo or media handling.
- Custom sport-specific generation rules beyond what the prompt naturally produces.

## References

- Strava API reference: https://developers.strava.com/docs/reference/
- Strava webhooks: https://developers.strava.com/docs/webhooks/
- Strava OAuth: https://developers.strava.com/docs/authentication/
- Anthropic API: https://docs.claude.com/en/api/getting-started
- Open-Meteo (free weather, no API key): https://open-meteo.com/en/docs
- Nominatim (free reverse geocoding, respect usage policy): https://nominatim.org/release-docs/develop/api/Reverse/

## Definition of Done (MVP)

- Service deployed and stable for 7 consecutive days.
- ≥10 activities renamed automatically without manual intervention.
- Every generated title passes a subjective "vibes check" from Juan.
- Original titles retrievable from logs for any renamed activity.
- README documents: authorize flow, deploy steps, how to disable, how to backfill, how to roll back.
