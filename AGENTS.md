# Trainyze — agent briefing

Shared context for any coding agent (Claude Code, Codex CLI, etc.) working in this repo.
Read this before making changes so you don't rediscover the same gotchas from scratch.

## What this is

Personal training dashboard: Flask backend (`garmin_server.py`) + vanilla HTML/JS
(`public/index.html`, `public/app.js`) + local PostgreSQL 17. Pulls Garmin Connect data,
gives AI-generated training recommendations, manages an adaptive training plan, syncs
Google Calendar, and shows the household's indoor climate (temperature + humidity) read
straight off Zigbee sensors.

Public site: **https://trainyze.com** (Cloudflare named tunnel).

## Where things run

- **Production on g3 runs branch `main`** (unified 2026-11-27; it used to run
  `redesign/on-prod` with uncommitted changes). Commit and push to `main`.
- How the whole g3 machine fits together (services, Bryggan, git-vakt backups,
  agent rules) is documented in `~/G3.md` on g3 (source: `HugoErixon/bryggan-2`
  `docs/G3.md`). Uncommitted work on g3 is snapshotted to `backup/g3/<branch>`.

- **Local dev clone:** this directory, on the machine `t490` (Tailscale hostname `t490`).
- **Production:** machine `g3` (Tailscale hostname `g3`, reachable via `ssh g3`), running
  as systemd service `dashboard.service`, local Postgres on the same box.
- Both t490 and g3 have their own clone of `HugoErixon/traning-dashbord` and push/pull via
  SSH deploy keys (alias `github.com-traning-dashbord` in each machine's `~/.ssh/config`) —
  **not** HTTPS. HTTPS clone is read-only.
- Custom slash commands drive the workflow: `/codex <task>` runs `codex exec` against this
  local clone, commits, and pushes. `/deploy` SSHes to g3, pulls, restarts
  `dashboard.service`, and verifies status.

## Two frontends: website and mobile app

Trainyze has two frontends against the **same API** (`garmin_server.py`):

- **Website**: `/` → `public/index.html`, `public/app.js`, `public/styles.css`.
- **Mobile app (PWA)**: `/app/` → `public/app/`. It started on 2026-11-28 as an **exact copy**
  of the website code that ran on trainyze.com (main `f737d5f`): `app.js`, `styles.css`,
  `ambient-fibers.js` byte-identical, `index.html` differing only in the manifest link
  (`/app/manifest.json`, `scope`/`start_url` `/app/`, standalone). From there it is developed
  on its own toward the best possible training app; the website is not touched by app work.
  The copy uses relative asset paths, so files must exist in `public/app/` (there's a test).
  `/app/` is always served (the page shows its own login; the API enforces auth). It reuses
  `/sw.js` (scope `/`) for push. Its files count toward `ASSET_VERSION`.
- Server decisions (today's verdict, readiness score) come from the API; don't recompute them
  in either client.
- The pages have no `viewport-fit=cover`, so on iPhone the home-screen app starts *below* the
  status bar, which takes the page's `theme-color`. The simulator reproduces that from the
  page's own meta tags. If you add `viewport-fit=cover` + `black-translucent`, the app has to
  handle safe areas itself, and the simulator then draws it behind the status bar.
- The CSP blocks inline scripts, so the inline theme script in `index.html` never runs in
  production. The dev server sends the same CSP, so the simulator shows the same behaviour.

**Developing the frontends on a laptop** (no Postgres/venv needed, only Python 3 + Node):

- `node tools/iphone-sim.mjs` opens the app in an **iPhone 15 Pro simulator** window
  (393×852, Dynamic Island, safe areas 59/34 pt, touch emulation and iPhone UA via Chrome
  DevTools Protocol) with live reload on file save. Add `--g3` for real data straight from
  Trainyze on g3 (port 3000 over Tailscale `100.82.234.62`, falling back to LAN
  `192.168.1.195`), or `--prod` for real data via trainyze.com. Both are read-only unless
  `--skriv`; you log in once with a normal account (cookie kept in the simulator's profile).
- `python3 tools/dev_shell.py` serves `/`, `/app/` and `/__iphone/` on `localhost:3100` with
  mock data. `--prod` proxies `/api/*` to trainyze.com **read-only** (only login/logout pass;
  writes return 403 `dev_readonly`); `--prod --skriv` allows writes. `--lan` exposes it to a
  phone on the same network/Tailscale.
- VS Code tasks in `.vscode/tasks.json` start both. The simulator strips VS Code's snap paths
  from the environment before launching Chrome; without that Chrome crashes on a GNOME
  schema mismatch ("antialiasing").

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
  `plan_sessions`, `health_history`, `metric_history`, `session_verdicts`, `sensor_readings`.
- **Climate:** the app subscribes to MQTT itself (`paho-mqtt`, topic `zigbee2mqtt/+`) and
  writes every reading to `sensor_readings`; `GET /api/climate` and `/api/climate/history`
  serve the Climate tab. This replaced `tuya-ac-keeper`, which was decommissioned on
  2026-08-04 — readings used to arrive through its HTTP API, so pulling AC control also
  pulled the temperature out of the UI. Three details worth keeping:
  - zigbee2mqtt republishes cached state on startup and those payloads carry **no
    `linkquality`**; they are dropped, otherwise old values get re-dated to now and a
    dead sensor looks alive.
  - The expected sensor roster comes from the retained `zigbee2mqtt/bridge/devices`
    topic, so a sensor that stops reporting is shown as silent instead of vanishing
    from the list. `Tempsensor_3` was dead for 44 days before anyone noticed.
  - The old `/api/ac*` routes are kept and answer **410 `ac_removed`** on purpose, so a
    stale open tab gets a real explanation rather than a 404 that looks like an outage.
- **Start page = Översikt** (`page-overview`, since 2026-10-08): long-term trends rather than
  the planned sessions, which the athlete found hard to plan around. `body_trends.py` serves
  `GET /api/overview?days=30|90|180`. Daily markers (HRV, resting pulse, sleep, Body Battery,
  stress, threshold pace) are compared as 7-day averages, not night against night; VO2max and
  endurance value against value. Threshold pace is averaged on purpose: Garmin flips between
  two values day to day (4:00/4:08). The "Just nu" cards (bevel triad) moved here from Idag.
  Since 2026-11-08 the page order is: race predictor → coach chat (moved here from Idag, same
  `#messages`/`#chat-input` ids and logic) → Just nu (CNS ring, HRV, resting pulse, last night's
  sleep) → "Veckans pass" (`#cockpit-next-list`, also moved from Idag: the rest of this week, or
  next week's sessions when this week is done) → trends.
- **Styrka (`page-strength`, top-level nav since 2026-11-09):** built for logging on the phone mid-session.
  `strength_overview.py` (pure) + `GET /api/strength/overview?days=30|90|180` give today's session key
  (today's Garmin strength activity id, else the date — `link_manual_exercises_to_activities` moves date
  rows onto the activity later), progress (estimated 1RM, Epley, from each session's best set; weekly
  volume = sets × reps × kg; records = a session beating everything logged before it) and recent sessions.
  `POST /api/strength/<session>/sets` logs **one set**; an identical set right after the session's latest
  row is merged into it (`sets + 1`), so history keeps the old "3 × 10 @ 40 kg" row shape and
  `strength_progression` keeps working. `PATCH /api/strength/exercises/<id>` edits a row (removing one set
  = `sets - 1`). Exercise cards not yet logged and typed values live in `localStorage`
  (`trainyze:strength:<date>`, cleared on logout) so a reload mid-workout loses nothing. On mobile the
  Styrka tab replaces Inställningar in the bottom bar (settings stay on the account button).
- **Training potential** (the "Vad kan mer träning ge?" scenario card, `training_potential.py`,
  `/api/potential`) was removed on 2026-11-08 at Hugo's request; the race predictor replaced it.
- **Race predictor (`race_predictor.py`, since 2026-11-08):** top of Översikt, `GET /api/race-predictor`.
  **Trainyze's own model, not Garmin's** (rebuilt the same day at Hugo's request — calibrating
  Garmin against one hot, hilly Göteborgsvarvet was not good enough). Every Garmin lap ≥ 6 min gives a point
  (avg HR, grade-adjusted speed). Interval reps of 2–6 min are added too, but scored by
  **end-of-rep HR (lap maxHR − 3)** rather than the average — HR lags pace in a short rep, so
  the average makes fast running look cheap. A rep only counts if it is ≥ 1.1× the median steady
  lap speed (jog recoveries labelled ACTIVE are not intervals) and maxHR ≤ avgHR + 20 (sensor
  spikes). Adding reps moved the pre-Varvet check from 1:19:50 to 1:26:06 (actual 1:26:45 GAP); laps from the last 42 days are fitted to speed = a + b·HR, weighted by
  duration and recency (half-life 21 d). HM pace = the line at **97 % of threshold HR** (Garmin's
  measured `lactate_hr`; declared assumption), other distances via Riegel 1.06. No prediction on
  days with < 20 steady laps, < 4 points within 8 bpm of that HR or a ±SE above 2.5 min — the line must not be
  stretched into the unknown (Aug–Sep 2026 had no such laps and gave 1:49). Races never adjust
  anything; they appear as "checks" (the day before Varvet the model said 1:26:06; he ran
  1:26:45 grade-adjusted in ~30 °C). Garmin's prediction and Garmin threshold pace × 1.03 are
  shown as comparison only (dashed line). The chart follows Översikt's period buttons (30 d / 90 d / 6 mån, filtered client-side); the stored history therefore covers 180 days (cache key bumped to `race-prediction:v3`). Laps are cached once per activity in `cache`
  (`laps:v2:<id>`, compact rows incl. maxHR) and backfilled during the background refresh, ≤ 40 per run.
  Target lives in cache `race-target:v1` (`PUT /api/race-target`); Hugo's is HM 1:22, date
  2027-03-14 (placeholder — military service starts end of March 2027). No LLM here.
  The result is **stored** (cache `race-prediction:v3` with a marker = run count + latest run id)
  and only recomputed when a new run arrives: `ingest_activities()` starts
  `refresh_race_prediction_async()` (background thread, one per user, forces a fresh Garmin
  fetch) as soon as a new run is saved. The marker is the safety net — if a page load sees it has
  changed, it serves the stored prediction with `refreshing: true` and starts the refresh; the
  page re-polls. Saving a target recomputes synchronously.
- **Race predictor on Strava (since 2026-11-09):** users without Garmin get the same model from Strava.
  `_race_source()` picks Garmin first (connected or runs in `activities`), else Strava, else a "connect
  Garmin or Strava" message. Strava activities live in cache `strava:history:v1` (a year, fetched page by
  page once, then incrementally from the latest run minus 2 days; `_strava_activities()` is a view of it,
  and a new run starts the prediction refresh). Laps come from `/activities/{id}/laps` into
  `strava-laps:v1:<id>` via `strava_integration.lap_rows()`: auto-laps (1 km / 1 mile) are merged in order
  into ≥ 6 min chunks unless pace differs > 12 %, manual laps stay as they are (marked ACTIVE; the
  predictor's speed filter drops jog recoveries). Strava has **no grade-adjusted speed**. Backfill is
  ≤ 30 runs per refresh and stops on 429 — Strava's rate limit is per app, shared by all users; the
  15-min sync keeps refreshing while runs still lack laps. Strava `workout_type == 1` → `eventType race`.
  Threshold HR: the user's own value (cache `threshold-hr:v1`, optional `lthr` in `PUT /api/race-target`,
  also overrides Garmin's for Hugo) else, for Strava, **estimated** = 90 % of the highest credible max HR
  of the last 180 days (`estimate_lthr`); the UI says which (`model.lthrSource`). Checked on Hugo's own
  Strava 2026-11-09: 1:23:50 with LTHR 181 vs 1:23:54 from Garmin; the estimate (177) gave 1:27:10.
  Disconnecting Strava deletes history, laps and the stored prediction (Strava terms).
  Strava limits how many athletes an API app may connect (shown in Strava's app settings); a second user
  had connected by 2026-11-09, so it is above 1, but check it before inviting many.
- **VDOT training paces (`vdot.py`, since 2026-11-20):** every running pace comes from one Daniels VDOT
  (Daniels–Gilbert formulas; checked against his tables — VDOT 50 gives T 4:15, I 3:55, 5 km 19:57).
  Zones: E = 62–74 % of VDOT, T = 88 %, I = 97.5 %, R = I − 15 s/km, M and HM = predicted race pace.
  Source order in `vdot.current()`: the user's own value (cache `vdot:v1`, `PUT /api/vdot`, empty =
  automatic) → the stored race predictor's half marathon → a race ≤ 60 days old (from the predictor's
  `checks`, GAP time when there is one) → Garmin threshold pace inverted. Only stored data is read, never
  Garmin. `_pace_context()` carries it as `vdot`; with a VDOT `pace_progression.target_band()` /
  `validate_proposal()` / `goal_feasibility()` use the Daniels zone (kind → zone: interval I, threshold T,
  race HM, easy/long/run E) and fall back to the old threshold offsets without one. Goal feasibility then
  compares against the zone's own pace, not the band edge. The zones go into `describe_anchor()` (review,
  coach chat, pace proposals, week draft), the plan-from-goal prompt (which had no paces at all before)
  and `ai_adjust_plan` via `_vdot_prompt_block()`; the coach briefing's fallback T/E paces too. The goal's
  VDOT comes from the race target (`race-target:v1`), else the goal text. UI: the "Måltempon & zoner"
  panel on Plan shows the zones, the goal's VDOT and an "Eget VDOT" field.
  Hugo on 2026-11-20: VDOT 56.3 (prediction 1:22:35), goal 1:22 = 56.8; Garmin threshold 4:02 = 53.5.
- **Plan weeks cross New Year (`plan_calendar.py`, since 2026-11-25):** `plan_sessions` has no year column,
  so `week` is a **plan week**: ISO week of 2026 that keeps counting (ISO week 1 2027 = plan week 54, Málaga
  2027-03-07 = 62). Every 2026 row stayed valid without a migration. Compare and store plan weeks; show
  `iso_week_label()` (JS `planWeekLabel()`, `isoWeekNumber()` for the app bar). JS `getISOWeek()` /
  `getMondayOfISOWeek()` now return plan weeks (the old names were kept so callers did not change). The coach
  room (`~/trainyze-coach/coach.py`) computes the same plan week.
- **Season plan (`season_plan.py`, Plan page, since 2026-11-25):** `GET /api/season-plan`, `POST /api/season-plan/rebuild`
  (optional `peakKm`). The frame for every week until the race in `race-target:v1`: phases counted back from the
  race week (half: race week, 1 taper, 2 "loppfart", 5 race-specific, the rest base + threshold), recovery week
  every 4th week back from the race (skipped next to Christmas, which is ~10 % lighter), volume ramping linearly
  from the base to the peak (half: min(base × 1.2, base + 14)), long runs to 21 km, 2–3 key sessions per week with
  paces from the current VDOT zones (goal pace in the sharpening phase). The base (4-week average before the
  start) is **frozen** in cache `season-plan:v1` so targets do not drift; rebuilt only when the race changes or
  on request. `season_plan.describe()` goes into `_goal_prompt_block()` (every planning prompt), and the week
  draft gets that week's frame explicitly. Each week also names its strength load (`STRENGTH_BY_PHASE`).
- **Lab test (`lab_tests.py`, Plan page, since 2026-11-25):** `GET/POST /api/lab-tests`, `DELETE /api/lab-tests/<date>`,
  cache `lab-tests:v1`. LT2 heart rate is written to `threshold-hr:v1` with `source: 'lab'` (race predictor
  `lthrSource: 'lab'`) and the prediction is recomputed; LT2 pace is a VDOT source after race and before Garmin's
  threshold pace. Five HR zones from LT1/LT2 go into the prompts. Pace accepts "3:58" or a treadmill "15,1 km/h".
- **Niggles (`niggles.py`, Översikt card, since 2026-11-25):** `GET/POST /api/niggles`, cache `niggles:v1`. One
  reading per area/side 0–10; the latest decides, 0 closes it, older than 28 days is inactive, ≥ 3 days old asks for
  a check-in. Active niggles go into every planning prompt and remove exercises from the strength programme
  (severity ≥ 3, `strength_program.AVOID_BY_AREA`).
- **Strength programme (`strength_program.py`, Styrka page, since 2026-11-25):** `GET /api/strength/program`. A
  (squat, split squat, calf raise, bench, row, dead bug) and B (deadlift, RDL, calf raise, OHP, pulldown, chins,
  back extension) in base/specific phases (fewer sets in specific), one maintenance session in sharpen/taper,
  none in race week, a lighter A in recovery weeks. Weights come from `strength_progression` on the log.
  **RIR** (reps in reserve) is an optional per-set column `strength_exercises.rir` (startup migration): RIR ≥ 3 on
  every top set raises two steps, RIR 0 holds the weight. Known exercises are stored and shown under the catalogue
  name (`strength_overview.display_name`), so "Bench press"/"Bänkpress" are one curve; the client gets the alias
  map in `/api/strength/overview` (`aliases`). `Vadpress` is now weight-progressed.
- **Weekly report (`weekly_report.py`, since 2026-11-25):** `GET /api/weekly-report` (Sundays the current week,
  otherwise last week) shown on Plan; push every Sunday 19:00 (`weekly_report_job`, cron with 3 h misfire grace,
  once per week via cache `weekly_report_sent`). Computed, not AI: km vs the season target, key sessions'
  execution verdicts, strength sessions vs target, other load (matches/referee duty logged as Ice Hockey), niggles,
  and next week's frame.
- **Coach chat prompt is built on the server (since 2026-11-25):** `_coach_system_prompt()` — the client no
  longer sends `context` (and the server ignores it, so an old tab cannot bring back "Max 3-4 meningar").
  Principles follow the Bryggan assistant's `training.md`; data: goal + season + lab + niggles + strength
  programme + predictor (`_goal_prompt_block`), recovery, today's rule-based recommendation, this week, planned
  sessions next 8 days, calendar 7 days, all activities 14 days, execution, feedback, notes, journal, paces. The
  chat runs `claude_cli` with `CLAUDE_CLI_CHAT_MODEL` (default `opus`) and `CLAUDE_CLI_CHAT_TIMEOUT` (90 s) via
  `call_llm(provider_overrides=...)`; other providers get 40 s. Replies may use Markdown (headings, bold, lists),
  rendered by `formatCoachReply()` after escaping. History caps raised to 24 messages / 24 000 chars.
- **Treadmill belt speeds (`treadmill.py`, since 2026-11-26):** on a treadmill the watch guesses pace from the
  wrist (Hugo's 4 × 2 km at exactly 15 km/h came back as 3:46–3:50/km, "faster than planned", +0.9 % fade). The
  athlete enters the belt speed per lap in the activity view (km/h or "4:00"; standing-rest laps < 50 m get none),
  `PUT /api/activities/<id>/treadmill`, cache `treadmill-speeds:v1:<id>`, keyed by Garmin lap position. It replaces
  speed and distance for those laps in `analyze_run` (`paceSource: 'belt'`), the activity detail and its AI overview,
  and the save recomputes `plan_sessions.execution` for that activity. Without speeds a treadmill run is judged on
  heart rate only: no pace, rep verdict or fade, flag `treadmill_speed_missing` (headline "Ange bandets fart").
- **Session analysis (`training_insights.py`):** `GET /api/training-insights`. Easy runs trend on
  meters per heartbeat (grade-adjusted speed / HR); threshold and intervals on *work-rep* pace from
  Garmin's `INTERVAL_ACTIVE` split summary. Structured sessions are re-sorted by rep length
  (≤ 5 min = intervals) because Garmin labels most short intervals TEMPO; single-rep "active"
  splits are the whole run and are ignored. Races stay out of the trends.
  The analysis cards and their endpoints were removed from the UI on 2026-11-08 at Hugo's request;
  the module now only feeds the coach. `_performance_prompt_block()` puts predictor + trends into `_goal_prompt_block()` (so every
  AI planning prompt sees them) and into the coach chat; it never fetches from Garmin.
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
  - **`claude_cli` (first in the chain on g3 since 2026-11-08)** runs the Claude Code CLI
    (`claude -p`) on Hugo's subscription instead of the paid API, the same way Bryggan does.
    It runs with no tools, no MCP, no setting sources, no saved session and slash commands
    off, from an empty temp dir; the system prompt goes via `--system-prompt-file` (too big
    for argv), history is written into the prompt as a transcript, and `json_schema` maps to
    `--json-schema`. `ANTHROPIC_API_KEY` is stripped from the child env — if it leaked
    through, the CLI would silently bill the API. Only user ids in `CLAUDE_CLI_USERS`
    (default `1`) may use it; everyone else falls through to the next provider, because it
    is the owner's personal quota. Usage-limit replies cool it down for
    `CLAUDE_CLI_LIMIT_COOLDOWN` (900 s). `CLAUDE_CLI_MODEL` defaults to `sonnet`.
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
  - **Chat memory:** `/api/assistant` takes a `history` of earlier turns in the same
    conversation, runs it through `normalize_history()`, and passes it to `call_llm(...,
    history=...)`, which every adapter sends as real chat turns. The client keeps the
    conversation in `sessionStorage` (`trainyze:chat`), so it survives reloads and
    navigation within the visit but not the next one, and `performLogout()` clears it —
    a shared computer must not hand the next user someone else's coach conversation.
    The history is untrusted input: it is capped at `CHAT_HISTORY_MAX_MESSAGES` /
    `CHAT_HISTORY_MAX_CHARS`, forced to alternate user/assistant, and may never start
    with an assistant turn or end with a user turn — Gemini and Anthropic reject the
    first, and the second would show the model the current question twice.
  - Follow-ups are routed with the conversation in hand: "flytta det till fredag" or
    "ja, kör på" counts as a plan change only when the coach's previous reply was about
    the plan (`_is_plan_change_request`), and `_plan_request_text()` hands `ai_adjust_plan`
    the last turns so it knows what "det" refers to. A plan change rewrites the schedule,
    so anything ambiguous must fall through to an ordinary chat answer instead.
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
- **One CNS number per day.** `_cns_score_from_health()` weights out missing parts instead of
  counting them as 50, and everything server-side reads health through `_current_health()`
  (cache → live Garmin via `_fetch_live_health()` → `health_history` snapshot). Before this,
  the sync emptied the cache every 15 minutes and background readers scored the snapshot
  without readiness or HRV component — the same night came out 91 on the page and 64 in the
  morning report (2026-10-20). The snapshot now carries `readiness` and `hrv_component`.
  Garmin returns several unsorted readiness entries per day (wake-up, realtime, after each
  workout); `_daily_readiness_entry()` takes the morning one, so the day's CNS stays put while
  the watch's own number drops after a session.
- **The night that has not synced yet.** Garmin publishes training readiness for the day
  well before the night's sleep is final, so `/api/health` deliberately falls back to the
  previous night to avoid an empty dashboard. That fallback is flagged on the sleep block
  (`sleep.fallback` / `sleep.sourceDate`), **not** on the payload root — only the DB
  snapshot from `latest_health_snapshot()` flags the root. Always ask
  `health_sleep_is_fallback()`; a plain `payload.get('fallback')` silently reads False for
  exactly the case the flag exists for. That bug wrote yesterday's sleep into today's
  `health_history` row, and the morning report and daily review then described it as
  "i natt" (seen 2026-08-05: a 3.9 h night reported on a day the athlete slept 11.7 h).
  `_recent_recovery()` returns `(cns, sleep_hours, sleep_source_date)` so callers can tell
  the difference — the morning report waits for the real night rather than reporting the
  old one, and `maybe_run_daily_routine()` collects history on readiness alone but holds
  the push until sleep lands (retrying on later syncs inside the morning window).
- **Plan adjustment pins exactly one session to today.** When the request says "idag", the
  coach otherwise moves the requested workout away for ACWR reasons, so the apply loop
  forces it onto today. That pin must hit a single change — `_change_to_pin_on_today()`
  picks it (an `add` if there is one, else the coach's first change). Pinning every change
  collapsed a whole week onto one day: on 2026-08-04 three sessions the coach had placed on
  Wednesday, Thursday and Tuesday all landed on Tuesday and were marked `missed` the next
  morning, leaving "Kommande 7 dagar" empty.
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
  button that could not work.
  - **Morning report** is the one live trigger. It hangs off `maybe_run_daily_routine()`,
    which already waits for today's sleep data to sync rather than guessing a time, so the
    report arrives once the numbers actually exist. `MORNING_REPORT_FROM`/`_TO` (5–11) stop a
    late sync from sending a "morning" report at night; a day outside the window is marked
    done rather than deferred. Text is computed, not AI-generated — a background job must not
    depend on LLM quota, and a lock screen has no room for reasoning anyway.
  - **Activity synced** fires from the shared Garmin ingestion path for genuinely new activities, carrying the
    deterministic `session_verdict` headline. Two guards matter more than the feature: only
    activities newer than `ACTIVITY_PUSH_MAX_AGE_HOURS` (30) qualify, and more than two at
    once collapse into a single summary. A backfill or first sync finds the whole history,
    and fifty notifications is a good way to make someone turn notifications off.
- **Email:** Resend (`RESEND_API_KEY`), domain `trainyze.com` verified via Cloudflare
  integration, used for registration verification emails.
- **Background sync:** runs every 15 minutes (configurable with `GARMIN_SYNC_MINUTES`, bounded
  to 5–180) and syncs Garmin,
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

**Schema `coach` (since 2026-11-14):** read-only views over Hugo's own rows (user_id 1),
owned by `dashboard`, read by the Postgres role `trainyze_coach` (SELECT on `coach.*` only,
read-only by default). They feed the Bryggan "Coach" room in `~/trainyze-coach`
(`coach.py` + coach briefing), which gives a Claude agent full access to the training data
without shell-level access to the app's credentials. `coach.cache` is an **allowlist** of
cache keys — never widen it to tokens or other users. Postgres will refuse to drop or retype
a column a view uses: drop/recreate the matching `coach.*` view in the same migration.
