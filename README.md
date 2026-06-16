# Training Dashboard

Personal training dashboard that fetches data from Garmin Connect.

## Requirements
- Python 3.10+
- Garmin Connect-konto

## Installation

1. Clone the repo
2. Install packages:
   ```
   pip install garminconnect flask requests python-dotenv
   ```
3. Copy `.env.example` to `.env` and fill in your details
4. Sign in to Garmin once:
   ```
   uvx --python 3.12 --from git+https://github.com/Taxuspt/garmin_mcp garmin-mcp-auth
   ```
5. Start the dashboard:
   ```
   python garmin_server.py
   ```
   Or double-click `Starta Dashboard.bat` on Windows

## Open
Go to [http://localhost:3000](http://localhost:3000)
