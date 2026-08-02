# Trainyze — agent briefing

Shared context for any coding agent (Claude Code, Codex CLI, etc.) working in this repo.
Read this before making changes so you don't rediscover the same gotchas from scratch.

## What this is

Personal training dashboard: Flask backend (`garmin_server.py`) + vanilla HTML/JS
(`public/index.html`, `public/app.js`) + local PostgreSQL 17. Pulls Garmin Connect data,
gives AI-generated training recommendations, manages an adaptive training plan, syncs
Google Calendar, and also drives an unrelated home AC-control system (Tuya + Zigbee via
`tuya-ac-keeper`) for the same household.

Public site: **https://trainyze.com** (Cloudflare named tunnel).

## Where things run

- **Local dev clone:** this directory, on the machine `t490` (Tailscale hostname `t490`).
- **Production:** machine `g3` (Tailscale hostname `g3`, reachable via `ssh g3`), running
  as systemd service `dashboard.service`, local Postgres on the same box.
- Both t490 and g3 have their own clone of `HugoErixon/traning-dashbord` and push/pull via
  SSH deploy keys (alias `github.com-traning-dashbord` in each machine's `~/.ssh/config`) —
  **not** HTTPS. HTTPS clone is read-only.
- Custom slash commands drive the workflow: `/codex <task>` runs `codex exec` against this
  local clone, commits, and pushes. `/deploy` SSHes to g3, pulls, restarts
  `dashboard.service`, and verifies status.

## Hard rules / known gotchas

- **`migrate_db.py` is a manual, one-off data-migration tool** — it requires an explicit
  target DB URL argument (`python migrate_db.py "<url>"`) and is **not** an idempotent
  schema migrator. Never run it automatically as part of a deploy. The app's own startup
  migration (logged as "Databas: migrering klar") runs automatically inside
  `garmin_server.py` and needs no separate action. (This bit us once on 2026-08-01 — a
  deploy script ran it blindly and it errored on a missing arg.)
- g3's sudoers rule only allows `systemctl restart/status/is-active dashboard.service`
  with **no extra flags** (e.g. no `--no-pager`) — anything else falls back to a password
  prompt.
- Local git history has, in the past, diverged from what's actually running in
  production (unclear exact cause). Don't assume `git push` alone guarantees production
  is updated — verify with `git log`/`git status` on g3 directly, and confirm behavior
  with `curl`/logs after any restart.
- Self-registered accounts must never default to admin (`is_admin=false`). Only the
  original bootstrap user (`hugo`, user id 1) is admin. Owner-only features (Climate tab,
  "Hantera användare") must stay gated both server-side (`uid()==1`) and in the frontend.
- A lot of config (goal-related constants, load-per-km tables, phase boundaries) is still
  hardcoded in multiple places — see "Known hardcoded values" below before assuming a
  single source of truth exists.

## Stack / key pieces

- **Backend:** `garmin_server.py` (Flask), `user_store.py` (DB vs in-memory user store).
- **DB tables:** `users`, `activities`, `cache`, `strength_exercises`, `user_notes`,
  `plan_sessions`, `health_history`, `metric_history`, `session_verdicts`.
- **Strain:** `strain_analysis.py` scores each day 0-100 by weighing that day's Garmin
  `activityTrainingLoad` against the athlete's own chronic load (Garmin's chronic value
  when available, otherwise a 28-day average) — a raw load number means nothing on its
  own. `GET /api/strain` serves it. The sync writes one `session_verdict` per newly seen
  activity from the last three days (`GET /api/session-verdict`); older activities showing
  up in a first-time sync are skipped on purpose, and existing verdicts are never
  overwritten since the first one was written in the context that applied that day.
- **AI:** `call_llm()` walks a provider chain set with `LLM_PROVIDERS=gemini,cerebras,groq`
  (ordered). It moves to the next provider only on quota (429) or network errors — a bad
  prompt or an unparseable reply fails immediately, since retrying it elsewhere would just
  burn a second quota. A provider that returns 429 is put on cooldown for the delay it
  asked for, so later requests skip straight past it instead of paying for the round trip;
  that cooldown is what actually lets two free tiers stack.
  - `gemini` and `anthropic` have their own adapters. `groq`, `cerebras`, `openrouter` and
    `mistral` all speak OpenAI chat-completions and share one adapter — add a key as
    `<NAME>_API_KEY`, and override `<NAME>_MODEL` / `<NAME>_URL` when needed. **Provider
    model names change often; the built-in defaults are a starting point, not a promise.**
  - **The chain is never built automatically.** With neither `LLM_PROVIDERS` nor the legacy
    `LLM_PROVIDER` set, exactly one provider is used. This is deliberate: g3 has a leftover
    `ANTHROPIC_API_KEY`, and auto-chaining to it would silently start spending money.
    Anthropic only ever runs if it is named explicitly.
  - `LLM_RETRY_MAX_WAIT` (default 10s) caps how long a provider may make us sleep before we
    give up on it; a wait is only taken at all when no other provider could take over.
  - 401/402/403 mean the account is unusable, not that the prompt is bad, so the chain moves
    on and parks that provider for `LLM_DISABLED_COOLDOWN` (default 1h) — asking again in
    thirty seconds cannot fix a missing payment method. Cerebras returned 402 on every model
    with a valid key on 2026-08-02, which is what this handling came from.
- **Daily review prompt** (`_build_review_prompt`): the athlete's goal, today's plan, today's
  activities with interval reps read from Garmin laps, a measured execution-vs-plan block,
  recovery and load (`_recovery_prompt_block`), the week so far (`_week_prompt_block`),
  and the athlete's own notes (`_notes_prompt_block`). Each context block swallows its own
  errors — a dead data source must never take the whole analysis down.
  - The answer carries `assessment`, `adjust` and `next` on top of `headline`/`body`.
    Bump `REVIEW_SCHEMA_VERSION` when that shape changes. Note the two separate checks in
    `training_review()`: serving the cache straight requires the *current* version, while the
    stale fallback accepts *any* version — an older answer still beats an error message.
- **Garmin auth:** unofficial `garminconnect` library per-user, tokens in
  `~/.garminconnect/<username>/`. Official aggregators (Terra, Junction) were evaluated
  and rejected as too expensive for this use case.
- **Web push:** `public/sw.js` (notifications only — it deliberately caches nothing, since a
  stale cache on a dashboard of today's numbers is worse than a slow load). Subscriptions live
  in `push_subscriptions`, keyed by endpoint so a re-subscribe updates instead of duplicating.
  `send_push(user_id, title, body, url)` fans out to every device and deletes subscriptions
  that answer 404/410 — those are gone for good. Needs `VAPID_PUBLIC_KEY` and
  `VAPID_PRIVATE_KEY` in `.env`; without them the endpoints report unavailable and nothing is
  sent. **On iPhone the site must be added to the Home Screen first** — Safari blocks push from
  an ordinary tab, so the settings card detects that case and says so instead of offering a
  button that could not work. No triggers are wired up yet beyond `POST /api/push/test`.
- **Email:** Resend (`RESEND_API_KEY`), domain `trainyze.com` verified via Cloudflare
  integration, used for registration verification emails.
- **Background sync:** runs every three hours from application startup and syncs Garmin,
  matches recent planned sessions against actual activities, and stores health/metric
  history once daily when Garmin has published fresh sleep or readiness data.
- **Plan adjustment:** runs only when a user explicitly asks the training assistant to
  change the plan. The LLM may then propose reschedule/skip/keep actions based on sleep,
  HRV, ACWR, calendar, etc.; there is no automatic morning plan-adjustment job.

## Known hardcoded values (not yet centralized)

- Goals (distance/time targets, deadlines) — several places in the code.
- VO2max / personal records — not auto-fetched from Garmin yet.
- CNS-score formula weights (0.40/0.30/0.20/0.10) — two places.
- Load-per-km tables and weekly-km/phase plan — a few places.
- Sleep goal (7.5h/night).

## Before you touch DB schema or deploy flow

Check the diff carefully for anything touching `plan_sessions`, `users`, or migration
logic, and ask Hugo which target DB URL to use if `migrate_db.py` genuinely needs to run
— don't guess or auto-run it.
