# Träningsdashboard

Personlig träningsdashboard som hämtar data från Garmin Connect.

## Krav
- Python 3.10+
- Garmin Connect-konto

## Installation

1. Klona repot
2. Installera paket:
   ```
   pip install garminconnect flask requests python-dotenv
   ```
3. Kopiera `.env.example` till `.env` och fyll i dina uppgifter
4. Logga in på Garmin (en gång):
   ```
   uvx --python 3.12 --from git+https://github.com/Taxuspt/garmin_mcp garmin-mcp-auth
   ```
5. Starta dashboarden:
   ```
   python garmin_server.py
   ```
   Eller dubbelklicka på `Starta Dashboard.bat` (Windows)

## Öppna
Gå till [http://localhost:3000](http://localhost:3000)
