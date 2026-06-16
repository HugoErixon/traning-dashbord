"""Engångsmigrering av all data från en Postgres-databas till en annan.

KÄLLA (source) = DATABASE_URL i .env (just nu Railway).
MÅL (dest)     = anges som argument.

Kör i projektmappen med venv aktiverat:

    python migrate_db.py "postgresql://dashboard:LÖSEN@localhost/traningdb"

Skriptet är idempotent (kan köras om) — det tömmer måltabellerna och kopierar om.
"""
import sys, psycopg2, psycopg2.extras
from dotenv import dotenv_values

cfg = dotenv_values('.env')
SRC = cfg.get('DATABASE_URL')
DST = sys.argv[1] if len(sys.argv) > 1 else None
if not SRC or not DST:
    raise SystemExit('Användning: python migrate_db.py "<mål-url>"  (källa tas från .env)')

TABLES = ['activities', 'cache', 'strength_exercises', 'user_notes', 'plan_sessions']
SERIAL_TABLES = ['strength_exercises', 'user_notes', 'plan_sessions']

DDL = [
    '''CREATE TABLE IF NOT EXISTS activities (id BIGINT PRIMARY KEY, name TEXT, date TEXT, type TEXT,
        distance REAL, duration REAL, avg_hr INTEGER, raw JSONB, created_at REAL)''',
    '''CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value JSONB, updated_at REAL)''',
    '''CREATE TABLE IF NOT EXISTS strength_exercises (id SERIAL PRIMARY KEY, session_id TEXT NOT NULL,
        exercise TEXT NOT NULL, sets INTEGER, reps TEXT, weight REAL, note TEXT, created_at REAL)''',
    '''CREATE TABLE IF NOT EXISTS user_notes (id SERIAL PRIMARY KEY, text TEXT NOT NULL,
        category TEXT DEFAULT 'general', created_at REAL)''',
    '''CREATE TABLE IF NOT EXISTS plan_sessions (id SERIAL PRIMARY KEY, week INTEGER NOT NULL, dow INTEGER NOT NULL,
        type TEXT NOT NULL, km REAL DEFAULT 0, title TEXT NOT NULL, detail TEXT DEFAULT '',
        status TEXT DEFAULT 'planned', original_week INTEGER, original_dow INTEGER, ai_note TEXT, modified_at REAL)''',
]

print('Ansluter...')
src = psycopg2.connect(SRC, sslmode='require')   # Railway/moln kräver SSL
dst = psycopg2.connect(DST)                       # lokal Postgres (ingen SSL)

# 1. Skapa tabellerna i målet
with dst.cursor() as c:
    for stmt in DDL:
        c.execute(stmt)
dst.commit()

# 2. Kopiera varje tabell
for t in TABLES:
    with src.cursor() as sc:
        sc.execute(f'SELECT * FROM {t}')
        cols = [d[0] for d in sc.description]
        rows = sc.fetchall()
    with dst.cursor() as dc:
        dc.execute(f'TRUNCATE {t} RESTART IDENTITY CASCADE')
        if rows:
            collist = ','.join(cols)
            ph = ','.join(['%s'] * len(cols))
            cleaned = []
            for r in rows:
                cleaned.append([psycopg2.extras.Json(v) if isinstance(v, (dict, list)) else v for v in r])
            psycopg2.extras.execute_batch(dc, f'INSERT INTO {t} ({collist}) VALUES ({ph})', cleaned)
    dst.commit()
    print(f'  {t}: {len(rows)} rader kopierade')

# 3. Återställ id-sekvenserna så nya rader inte krockar
with dst.cursor() as dc:
    for t in SERIAL_TABLES:
        dc.execute(f"SELECT setval(pg_get_serial_sequence('{t}','id'), COALESCE((SELECT MAX(id) FROM {t}), 1))")
dst.commit()

src.close()
dst.close()
print('Klart! All data migrerad.')
