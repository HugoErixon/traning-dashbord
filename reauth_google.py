"""Logga in mot Google Calendar på nytt och skriv en färsk google_token.json.

Kör lokalt i projektmappen:

    python reauth_google.py

En webbläsare öppnas där du godkänner åtkomst. Behöver göras om var 7:e dag
så länge OAuth-appen står i "Testing" i Google Cloud Console — publicera appen
(Publishing status -> In production) för att slippa det.
"""
import os
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
CREDS  = 'google_credentials.json'
TOKEN  = 'google_token.json'

if not os.path.exists(CREDS):
    raise SystemExit(f'Hittar inte {CREDS} — kör skriptet i projektmappen.')

flow = InstalledAppFlow.from_client_secrets_file(CREDS, SCOPES)
creds = flow.run_local_server(port=0)
with open(TOKEN, 'w') as f:
    f.write(creds.to_json())

print('Klart! Ny token sparad i', TOKEN)
print('Giltig till:', creds.expiry)
