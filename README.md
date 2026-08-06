# Training Dashboard

Personal training dashboard that fetches data from Garmin Connect or Strava.

## Requirements

- Python 3.10+
- PostgreSQL
- Garmin Connect account

Strava is optional. When both Garmin and Strava are connected, Garmin is used as
the primary activity source to avoid duplicate workouts.

## Installation

1. Create a virtual environment and install the pinned dependencies:

   ```powershell
   python -m venv venv
   .\venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and configure the database and integrations.

3. Generate a password hash for each dashboard user:

   ```powershell
   .\venv\Scripts\python.exe -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass('Password: ')))"
   ```

   Store accounts as `USERS=username:password_hash`. Separate multiple accounts with commas.

4. Generate a stable session secret:

   ```powershell
   .\venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
   ```

   Set the result as `SESSION_SECRET`. Use `SESSION_COOKIE_SECURE=true` and
   `ENABLE_HSTS=true` when the dashboard is only served over HTTPS.

5. Sign in to Garmin once:

   ```powershell
   uvx --python 3.12 --from git+https://github.com/Taxuspt/garmin_mcp garmin-mcp-auth
   ```

   Alternatively, configure Strava OAuth by creating an application at
   `https://www.strava.com/settings/api`. Set the callback domain to the public
   Trainyze hostname, then add `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, and
   `STRAVA_REDIRECT_URI=https://your-host/strava/callback` to `.env`. Each user
   can then connect their own Strava account from the dashboard. Access and
   refresh tokens are stored server-side with owner-only file permissions.

6. Start the dashboard:

   ```powershell
   .\venv\Scripts\python.exe garmin_server.py
   ```

## Tests

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

The authentication tests run without a database, Garmin login, or background jobs.

## Owner-only AI Control on g3

`/ai` is a separate security zone for owner-approved development jobs. It requires
the bootstrap admin account (user id 1), a WebAuthn passkey with user verification
(Face ID on iPhone), the existing CSRF-protected session, and a recent passkey
step-up. The passkey session expires after ten minutes by default.

The web server never opens a terminal socket. It stores a job in PostgreSQL and a
separate worker polls the API with a high-entropy bearer token. Start a prompt with
`/codex` or `/claude`; a prompt without a command defaults to Codex. Both tools run
in the dedicated checkout with no interactive approvals and explicit instructions
not to use sudo, deploy, restart, commit, or push.

### Server configuration

Generate two different secrets (do not reuse `SESSION_SECRET`):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Configure the values described in `.env.example`, install `requirements.txt`, and
restart the dashboard. Keep `AI_CONTROL_ENABLED=false` until the separate worker
checkout and service are ready. On the first `/ai` visit, enter
`AI_PASSKEY_BOOTSTRAP_TOKEN` to register Face ID. Remove the bootstrap token from
the environment after successful registration; existing passkeys keep working.

### Worker isolation

Create a dedicated `trainyze-agent` OS user and a separate Git checkout such as
`/opt/trainyze-agent/workspace`. Never point `AI_AGENT_WORKSPACE` at the live
dashboard checkout. Give the worker no sudo rule and no production `.env` access.
Its environment file should be owner-readable only and contain:

```dotenv
AI_AGENT_BASE_URL=http://127.0.0.1:3000
AI_AGENT_TOKEN=the-same-separate-agent-secret
AI_AGENT_WORKSPACE=/opt/trainyze-agent/workspace
AI_AGENT_ID=g3-workspace
AI_AGENT_JOB_TIMEOUT=1800
```

Use `tools/trainyze-ai-agent.service.example` as the hardened systemd template.
Install `@openai/codex` and `@anthropic-ai/claude-code` for the worker account.
Codex authentication belongs under `/var/lib/trainyze-agent/codex` and Claude under
`/var/lib/trainyze-agent/claude`; authenticate both locally on g3 before enabling
the service. Generated changes remain in the separate checkout for review.
Deployment stays outside the worker and uses the normal reviewed deploy flow.
