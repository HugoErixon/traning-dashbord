# Training Dashboard

Personal training dashboard that fetches data from Garmin Connect.

## Requirements

- Python 3.10+
- PostgreSQL
- Garmin Connect account

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

6. Start the dashboard:

   ```powershell
   .\venv\Scripts\python.exe garmin_server.py
   ```

## Tests

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

The authentication tests run without a database, Garmin login, or background jobs.
