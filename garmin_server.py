from flask import Flask, request, jsonify, send_from_directory, g as flask_g
from garminconnect import Garmin
from pathlib import Path
from dotenv import dotenv_values
import json, time, requests, psycopg2, psycopg2.extras, subprocess, threading
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import os
import yaml
import re
from apscheduler.schedulers.background import BackgroundScheduler

# Google Calendar (valfritt — kräver google_credentials.json)
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request as GRequest
    from googleapiclient.discovery import build as gbuild
    GCAL_AVAILABLE = True
except ImportError:
    GCAL_AVAILABLE = False

app = Flask(__name__, static_folder='public')

config = dotenv_values('.env')
_raw_users = config.get('USERS', 'hugo:hugo123')
USERS = {}
for i, entry in enumerate(_raw_users.split(','), start=1):
    parts = entry.strip().split(':', 1)
    if len(parts) == 2:
        USERS[parts[0].strip()] = {'id': i, 'password': parts[1].strip()}
# Backward-compat alias: first user's password (or legacy SITE_PASSWORD)
PASSWORD = config.get('SITE_PASSWORD') or (list(USERS.values())[0]['password'] if USERS else 'hugo123')
ANTHROPIC_KEY = config.get('ANTHROPIC_API_KEY', '')
TOKEN_DIR     = str(Path.home() / '.garminconnect')
DATABASE_URL  = config.get('DATABASE_URL', '')
GCAL_ID       = config.get('GOOGLE_CALENDAR_ID', 'primary')
GCAL_CREDS    = 'google_credentials.json'
GCAL_SCOPES   = ['https://www.googleapis.com/auth/calendar.readonly']
LOCAL_TZ      = ZoneInfo('Europe/Stockholm')

def uid():
    return getattr(flask_g, 'uid', 1)

def uname():
    return getattr(flask_g, 'uname', list(USERS.keys())[0] if USERS else 'hugo')

def gcal_token():
    return f'google_token_{uname()}.json'

AC_KEEPER_URL = config.get('AC_KEEPER_URL', 'http://127.0.0.1:8089')
AC_LOOP_SERVICE = config.get('AC_LOOP_SERVICE', 'ac-keeper-loop')
AC_CONTROL_FLAG = config.get('AC_CONTROL_FLAG', '/home/hugoerixon/tuya-ac-keeper/data/control_enabled')
AC_KEEPER_CONFIG = config.get('AC_KEEPER_CONFIG', '/home/hugoerixon/tuya-ac-keeper/config.yaml')
WEATHER_LAT = float(config.get('WEATHER_LAT', '58.35593'))
WEATHER_LON = float(config.get('WEATHER_LON', '11.22411'))
WEATHER_LOCATION = config.get('WEATHER_LOCATION', 'Smögen')

WEATHER_CODES = {
    0: 'klart',
    1: 'mest klart',
    2: 'halvklart',
    3: 'mulet',
    45: 'dimma',
    48: 'rimfrost-dimma',
    51: 'lätt duggregn',
    53: 'duggregn',
    55: 'kraftigt duggregn',
    61: 'lätt regn',
    63: 'regn',
    65: 'kraftigt regn',
    71: 'lätt snöfall',
    73: 'snöfall',
    75: 'kraftigt snöfall',
    80: 'lätta regnskurar',
    81: 'regnskurar',
    82: 'kraftiga regnskurar',
    95: 'åska',
}

def _get_outdoor_temperature_history(hours=24):
    """Hämta utetemperatur för grafen. Fel här ska inte slå ut rumstempgrafen."""
    end = datetime.now(LOCAL_TZ)
    start = end - timedelta(hours=hours)
    try:
        params = {
            'latitude': WEATHER_LAT,
            'longitude': WEATHER_LON,
            'hourly': 'temperature_2m',
            'timezone': 'auto',
            'start_date': start.date().isoformat(),
            'end_date': end.date().isoformat(),
        }
        r = requests.get('https://api.open-meteo.com/v1/forecast', params=params, timeout=6)
        r.raise_for_status()
        hourly = (r.json() or {}).get('hourly') or {}
        times = hourly.get('time') or []
        temps = hourly.get('temperature_2m') or []
        points = []
        for ts, temp in zip(times, temps):
            if temp is None:
                continue
            dt = datetime.fromisoformat(ts).replace(tzinfo=LOCAL_TZ)
            if start <= dt <= end:
                points.append({'t': dt.isoformat(), 'temp': temp})
        return points
    except Exception as e:
        print('weather history unavailable:', e)
        return []

# --- Databas ---
def db():
    conn = psycopg2.connect(DATABASE_URL, sslmode='prefer')
    conn.autocommit = False
    return conn

def setup_db():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''CREATE TABLE IF NOT EXISTS activities (
                id BIGINT PRIMARY KEY, name TEXT, date TEXT, type TEXT,
                distance REAL, duration REAL, avg_hr INTEGER,
                raw JSONB, created_at REAL)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY, value JSONB, updated_at REAL)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS strength_exercises (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                exercise TEXT NOT NULL,
                sets INTEGER,
                reps TEXT,
                weight REAL,
                note TEXT,
                created_at REAL)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS user_notes (
                id SERIAL PRIMARY KEY,
                text TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                created_at REAL)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS journal_entries (
                id SERIAL PRIMARY KEY,
                entry_date TEXT NOT NULL,
                mood TEXT DEFAULT '',
                energy INTEGER,
                text TEXT NOT NULL,
                created_at REAL,
                updated_at REAL,
                user_id INTEGER DEFAULT 1,
                UNIQUE(entry_date, user_id))''')
            cur.execute('''CREATE TABLE IF NOT EXISTS plan_sessions (
                id SERIAL PRIMARY KEY,
                week INTEGER NOT NULL,
                dow INTEGER NOT NULL,
                type TEXT NOT NULL,
                km REAL DEFAULT 0,
                title TEXT NOT NULL,
                detail TEXT DEFAULT '',
                status TEXT DEFAULT 'planned',
                original_week INTEGER,
                original_dow INTEGER,
                ai_note TEXT,
                modified_at REAL)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS health_history (
                date TEXT PRIMARY KEY,
                sleep_score INTEGER, sleep_hours REAL, deep_pct INTEGER, rem_pct INTEGER,
                hrv_avg INTEGER, resting_hr INTEGER, readiness INTEGER, body_battery INTEGER,
                stress_avg INTEGER, created_at REAL)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS metric_history (
                date TEXT PRIMARY KEY,
                vo2max REAL, endurance_score INTEGER,
                lactate_hr INTEGER, lactate_pace REAL,
                hrv_status TEXT, created_at REAL)''')
        conn.commit()
    print('Databas: klar')

def migrate_db():
    with db() as conn:
        with conn.cursor() as cur:
            for tbl in ('activities', 'user_notes', 'journal_entries', 'plan_sessions', 'strength_exercises',
                        'health_history', 'metric_history'):
                try:
                    cur.execute(f'ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS user_id INTEGER DEFAULT 1')
                except Exception as e:
                    print(f'migrate_db {tbl} user_id:', e)
            try:
                cur.execute('ALTER TABLE health_history ADD COLUMN IF NOT EXISTS body_battery INTEGER')
            except Exception as e:
                print('migrate_db health_history body_battery:', e)
            try:
                cur.execute('ALTER TABLE health_history ADD COLUMN IF NOT EXISTS stress_avg INTEGER')
            except Exception as e:
                print('migrate_db health_history stress_avg:', e)
            for tbl in ('health_history', 'metric_history'):
                try:
                    cur.execute(f'ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS {tbl}_pkey')
                    cur.execute(f'ALTER TABLE {tbl} ADD PRIMARY KEY (date, user_id)')
                except Exception as e:
                    print(f'migrate_db {tbl} pk:', e)
            try:
                cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS journal_entries_date_user_idx ON journal_entries (entry_date, user_id)')
            except Exception as e:
                print('migrate_db journal_entries unique:', e)
        conn.commit()
    print('Databas: migrering klar')

try:
    setup_db()
    migrate_db()
except Exception as e:
    print('Databas fel:', e)

# --- Garmin ---
# Token migration note for Pi: if Hugo's existing tokens are at ~/.garminconnect/,
# run: mv ~/.garminconnect ~/.garminconnect_bak && mkdir ~/.garminconnect && mv ~/.garminconnect_bak ~/.garminconnect/hugo
_garmin_clients = {}

def get_garmin(username=None):
    global _garmin_clients
    if username is None:
        username = uname()
    if username in _garmin_clients:
        return _garmin_clients[username]
    token_dir = str(Path.home() / '.garminconnect' / username)
    Path(token_dir).mkdir(parents=True, exist_ok=True)
    g = Garmin()
    try:
        g.login(tokenstore=token_dir)
    except Exception:
        # Fallback to legacy path for the first user (backward compat)
        first_user = list(USERS.keys())[0] if USERS else 'hugo'
        if username == first_user:
            g = Garmin()
            g.login(tokenstore=TOKEN_DIR)
        else:
            raise
    _garmin_clients[username] = g
    return g

def save_activities(activities, user_id=1):
    with db() as conn:
        with conn.cursor() as cur:
            for a in activities:
                try:
                    cur.execute('''INSERT INTO activities (id,name,date,type,distance,duration,avg_hr,raw,created_at,user_id)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (id) DO UPDATE SET raw=EXCLUDED.raw, name=EXCLUDED.name''',
                        (a.get('activityId'), a.get('activityName'), a.get('startTimeLocal'),
                         a.get('activityType', {}).get('typeKey'),
                         a.get('distance'), a.get('duration'), a.get('averageHR'),
                         json.dumps(a), time.time(), user_id))
                except Exception as e:
                    print('Spara aktivitet fel:', e)
        conn.commit()

def get_cache(key, user_id=1):
    prefixed = f'{user_id}:{key}'
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value, updated_at FROM cache WHERE key=%s", (prefixed,))
            return cur.fetchone()

def set_cache(key, value, user_id=1):
    prefixed = f'{user_id}:{key}'
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO cache (key, value, updated_at) VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at''',
                (prefixed, json.dumps(value), time.time()))
        conn.commit()

def clear_cache(*keys, user_id=1):
    prefixed = [f'{user_id}:{k}' for k in keys]
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM cache WHERE key = ANY(%s)", (prefixed,))
        conn.commit()

# --- Auth ---
@app.before_request
def check_auth():
    if not request.path.startswith('/api/'):
        return
    if request.path == '/api/login':
        return
    if request.host.startswith('localhost') or request.host.startswith('127.0.0.1'):
        first = list(USERS.keys())[0] if USERS else 'hugo'
        flask_g.uid = USERS.get(first, {}).get('id', 1)
        flask_g.uname = first
        return
    username = request.headers.get('x-site-user', '')
    password = request.headers.get('x-site-password', '')
    if username and username in USERS:
        if USERS[username]['password'] == password:
            flask_g.uid = USERS[username]['id']
            flask_g.uname = username
            return
    else:
        # Backward compat: no username header → check against first user
        first = list(USERS.keys())[0] if USERS else 'hugo'
        if password == USERS.get(first, {}).get('password', PASSWORD):
            flask_g.uid = USERS.get(first, {}).get('id', 1)
            flask_g.uname = first
            return
    return jsonify({'error': 'Unauthorized'}), 401

# --- Endpoints ---
@app.post('/api/login')
def login():
    data = request.json or {}
    username = data.get('username', '')
    password = data.get('password', '')
    if username and username in USERS:
        if USERS[username]['password'] == password:
            return jsonify({'ok': True, 'username': username, 'user_id': USERS[username]['id']})
    else:
        # Backward compat: no username → check first user
        first = list(USERS.keys())[0] if USERS else 'hugo'
        if password == USERS.get(first, {}).get('password', PASSWORD):
            return jsonify({'ok': True, 'username': first, 'user_id': USERS.get(first, {}).get('id', 1)})
    return jsonify({'ok': False}), 401

@app.get('/api/status')
def status():
    return jsonify({'status': 'ok'})

@app.get('/api/weather/current')
def current_weather():
    """Aktuell utetemperatur från Open-Meteo."""
    try:
        params = {
            'latitude': WEATHER_LAT,
            'longitude': WEATHER_LON,
            'current': 'temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m',
            'timezone': 'auto',
            'wind_speed_unit': 'ms',
        }
        r = requests.get('https://api.open-meteo.com/v1/forecast', params=params, timeout=6)
        r.raise_for_status()
        payload = r.json()
        current = payload.get('current') or {}
        units = payload.get('current_units') or {}
        code = current.get('weather_code')
        return jsonify({
            'ok': True,
            'source': 'Open-Meteo',
            'location': WEATHER_LOCATION,
            'latitude': WEATHER_LAT,
            'longitude': WEATHER_LON,
            'time': current.get('time'),
            'temperature_c': current.get('temperature_2m'),
            'apparent_temperature_c': current.get('apparent_temperature'),
            'humidity_pct': current.get('relative_humidity_2m'),
            'wind_speed_ms': current.get('wind_speed_10m'),
            'weather_code': code,
            'weather_text': WEATHER_CODES.get(code, 'okänt väderläge'),
            'units': units,
        })
    except Exception as e:
        return jsonify({'ok': False, 'source': 'Open-Meteo', 'error': str(e)}), 502

@app.get('/api/ac')
def ac_proxy():
    """Hämtar aktuell temperatur/AC-status från ac-keeper (på Pi:n via localhost)."""
    if uid() != 1:
        return jsonify({'available': False, 'error': 'AC control only available to owner'}), 403
    try:
        r = requests.get(f'{AC_KEEPER_URL}/api/current', timeout=4)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'available': False, 'error': str(e)})

@app.get('/api/ac/history')
def ac_history():
    """Rumstemperatur + utetemperatur senaste 24h för klimatgrafen."""
    if uid() != 1:
        return jsonify({'available': False, 'error': 'AC control only available to owner'}), 403
    try:
        r = requests.get(f'{AC_KEEPER_URL}/api/control-events', params={'hours': 24}, timeout=6)
        events = r.json()
    except Exception as e:
        return jsonify({'available': False, 'error': str(e), 'points': []})
    pts = [{'t': e['ts'], 'temp': e['measured_c']} for e in events if e.get('measured_c') is not None]
    if len(pts) > 180:
        step = len(pts) // 180 + 1
        pts = pts[::step]
    # AC-lägesändringar (på/av + setpoint) från den fulla event-listan
    markers = []
    prev_cool = None
    prev_sp = None
    for e in events:
        act = (e.get('action') or '').replace('dry_run_', '')
        cool = (act == 'cool')
        sp = e.get('requested_setpoint_c')
        if prev_cool is not None:
            if cool != prev_cool:
                if cool:
                    lab = 'AC on' + (f', setpoint {sp:.0f}°' if sp is not None else '')
                    markers.append({'t': e['ts'], 'kind': 'on', 'label': lab})
                else:
                    markers.append({'t': e['ts'], 'kind': 'off', 'label': 'AC off'})
            elif cool and sp is not None and prev_sp is not None and sp != prev_sp:
                markers.append({'t': e['ts'], 'kind': 'setpoint', 'label': f'Setpoint → {sp:.0f}°'})
        prev_cool = cool
        prev_sp = sp
    target = events[-1].get('target_c') if events else None
    return jsonify({
        'available': True,
        'points': pts,
        'outside_points': _get_outdoor_temperature_history(24),
        'outside_location': WEATHER_LOCATION,
        'target': target,
        'markers': markers,
    })

def _read_control_flag():
    """Är AC-STYRNINGEN aktiverad? (flagg-fil; saknas → på). Loopen loggar alltid."""
    try:
        with open(AC_CONTROL_FLAG) as f:
            return f.read().strip().lower() not in ('0', 'false', 'off', 'no', '')
    except FileNotFoundError:
        return True
    except Exception:
        return True

def _ac_loop_status():
    try:
        res = subprocess.run(
            ['systemctl', 'is-active', AC_LOOP_SERVICE],
            capture_output=True, text=True, timeout=4
        )
        running = (res.stdout or '').strip() == 'active'
    except Exception:
        running = False
    return {
        'available': True,
        'service': AC_LOOP_SERVICE,
        'enabled': _read_control_flag(),   # styr om AC:n kommenderas
        'running': running,                # loggar-loopen igång?
    }

@app.get('/api/ac/loop')
def ac_loop_status():
    if uid() != 1:
        return jsonify({'available': False, 'error': 'AC control only available to owner'}), 403
    return jsonify(_ac_loop_status())

@app.post('/api/ac/loop')
def ac_loop_control():
    if uid() != 1:
        return jsonify({'available': False, 'error': 'AC control only available to owner'}), 403
    data = request.json or {}
    enabled = bool(data.get('enabled'))
    try:
        os.makedirs(os.path.dirname(AC_CONTROL_FLAG), exist_ok=True)
        with open(AC_CONTROL_FLAG, 'w') as f:
            f.write('1' if enabled else '0')
        status = _ac_loop_status()
        status['ok'] = True
        return jsonify(status)
    except Exception as e:
        return jsonify({
            'ok': False,
            'available': False,
            'service': AC_LOOP_SERVICE,
            'enabled': _read_control_flag(),
            'error': str(e),
        }), 500

@app.post('/api/ac/setpoint')
def ac_setpoint():
    """Uppdaterar target_c i ac-keepers config.yaml och startar om loopen."""
    if uid() != 1:
        return jsonify({'available': False, 'error': 'AC control only available to owner'}), 403
    data = request.json or {}
    try:
        target = float(data['target_c'])
    except (KeyError, ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'target_c saknas eller ogiltigt'}), 400
    if not (10.0 <= target <= 35.0):
        return jsonify({'ok': False, 'error': 'Temperatur måste vara 10–35 °C'}), 400
    try:
        with open(AC_KEEPER_CONFIG, 'r') as f:
            cfg = yaml.safe_load(f) or {}
        if 'controller' not in cfg:
            cfg['controller'] = {}
        cfg['controller']['target_c'] = round(target * 2) / 2
        with open(AC_KEEPER_CONFIG, 'w') as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
        result = subprocess.run(['sudo', 'systemctl', 'restart', AC_LOOP_SERVICE], timeout=10, capture_output=True)
        if result.returncode != 0:
            return jsonify({'ok': False, 'error': 'systemctl restart failed: ' + result.stderr.decode()}), 500
        return jsonify({'ok': True, 'target_c': cfg['controller']['target_c']})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.get('/api/activities')
def activities():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT raw FROM activities WHERE user_id=%s ORDER BY date DESC LIMIT 50', (uid(),))
            rows = cur.fetchall()
    if rows:
        return jsonify({'activities': [r[0] for r in rows], 'source': 'database'})
    try:
        client = get_garmin(uname())
        acts = client.get_activities(0, 50)
        save_activities(acts, uid())
        return jsonify({'activities': acts, 'source': 'garmin'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─────────────────────────────────────────────
# HRV-LOGIK (Garmin HRV Status + personlig baslinje)
# ─────────────────────────────────────────────
# Garmin returnerar:
#   status: BALANCED / UNBALANCED / LOW / POOR / NONE  (trend över 7-dygns-snitt mot baslinje)
#   baseline: { lowUpper, balancedLow, balancedUpper }  (din personliga balanced-range)
# Primär signal = Garmins status (samma symbol som i Garmin Connect-appen).
# Sekundär finmätare = gårnattens HRV relativt baslinjebandet + råförhållande mot veckosnitt.

HRV_STATUS_LIGHT = {       # status → trafikljus
    'BALANCED':   'green',
    'UNBALANCED': 'amber',
    'LOW':        'red',
    'POOR':       'red',
    'NONE':       None,
}
HRV_STATUS_CAP = {         # status → taklimit för HRV-komponenten i CNS (trendstraff)
    'BALANCED':   100,
    'UNBALANCED':  80,
    'LOW':         60,
    'POOR':        45,
    'NONE':        None,
}
HRV_STATUS_VERDICT = {     # status → kort verdikt
    'BALANCED':   'HRV balanserad — autonoma nervsystemet ligger i ditt normala spann',
    'UNBALANCED': 'HRV i obalans — utanför ditt normala spann, träna med viss försiktighet',
    'LOW':        'HRV låg — under baslinjen, prioritera återhämtning',
    'POOR':       'HRV mycket låg — längre låg trend, vila rekommenderas',
    'NONE':       'Inte tillräckligt med baslinjedata ännu',
}

def hrv_component(last_night, low_upper, balanced_low, status, raw_pct):
    """
    HRV-komponent (0–100) för CNS-scoren.
    Bygger på gårnattens HRV relativt din personliga baslinje (samma som Garmins nattprick),
    med ett tak baserat på Garmins trendstatus. Faller tillbaka på råförhållande om baslinje saknas.
    """
    pos = None
    if last_night and balanced_low and low_upper:
        if last_night >= balanced_low:
            pos = 100.0
        elif last_night >= low_upper:
            span = balanced_low - low_upper
            pos = 70 + 30 * (last_night - low_upper) / span if span else 85.0
        else:
            pos = max(25.0, 70 * last_night / low_upper)
    cap = HRV_STATUS_CAP.get((status or 'NONE').upper())
    if pos is None:
        # Ingen baslinje → använd råförhållande (gammal metod) som fallback
        if raw_pct is None:
            return cap  # kan vara None
        pos = min(raw_pct, 100)
    if cap is None:
        return round(pos)
    return round(min(pos, cap))

def hrv_signal(status, last_night, weekly):
    """
    Returnerar (light, verdict) för trafikljuset.
    Primärt Garmins status; om den saknas faller vi tillbaka på Kiviniemi ±5% mot veckosnitt.
    """
    st = (status or 'NONE').upper()
    light = HRV_STATUS_LIGHT.get(st)
    if light:
        return light, HRV_STATUS_VERDICT.get(st, st.title())
    # Fallback: råförhållande
    if last_night and weekly:
        diff = (last_night - weekly) / weekly * 100
        if diff >= 5:   return 'green', f'HRV +{diff:.0f}% vs weekly avg — train hard'
        if diff <= -5:  return 'red',   f'HRV {diff:.0f}% vs weekly avg — rest or Z2'
        return 'amber', f'HRV {diff:+.0f}% vs weekly avg — normal session'
    return 'amber', 'HRV data unavailable'


def safe_health_fetch(label, default, fetcher):
    try:
        value = fetcher()
        return default if value is None else value
    except Exception as e:
        print(f'Garmin health {label} unavailable: {e}', flush=True)
        return default


def has_health_payload(result):
    return any([
        result.get('readiness', {}).get('score') is not None,
        result.get('hrv', {}).get('lastNightAvg') is not None,
        result.get('restingHR', {}).get('value') is not None,
        result.get('sleep', {}).get('totalSec') is not None,
        result.get('bodyBattery', {}).get('max') is not None,
        result.get('stress', {}).get('avg') is not None,
        result.get('respiration', {}).get('avg') is not None,
        result.get('spo2', {}).get('avg') is not None,
    ])


def has_sleep_levels(result):
    return bool(((result or {}).get('sleep') or {}).get('levels'))


def latest_health_snapshot(user_id, display_date):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT date, sleep_score, sleep_hours, deep_pct, rem_pct,
                                  hrv_avg, resting_hr, body_battery, stress_avg
                           FROM health_history
                           WHERE user_id=%s AND (
                               sleep_score IS NOT NULL OR sleep_hours IS NOT NULL OR
                               hrv_avg IS NOT NULL OR resting_hr IS NOT NULL OR
                               body_battery IS NOT NULL OR stress_avg IS NOT NULL
                           )
                           ORDER BY date DESC LIMIT 1''', (user_id,))
            row = cur.fetchone()
    if not row:
        return None

    source_date, sleep_score, sleep_hours, deep_pct, rem_pct, hrv_avg, resting_hr, body_battery, stress_avg = row
    total_sec = round(float(sleep_hours) * 3600) if sleep_hours is not None else None
    return {
        'date': display_date,
        'sourceDate': source_date.isoformat() if hasattr(source_date, 'isoformat') else source_date,
        'fallback': True,
        'readiness': {'score': None, 'level': None, 'feedback': None},
        'hrv': {'lastNightAvg': hrv_avg, 'weeklyAvg': None, 'status': None, 'pct': None,
                'balancedLow': None, 'balancedUpper': None, 'lowUpper': None,
                'component': None, 'light': 'amber', 'verdict': 'Senaste sparade HRV'},
        'restingHR': {'value': resting_hr, 'sevenDayAvg': None, 'min': None},
        'sleep': {'totalSec': total_sec, 'deepSec': None, 'remSec': None, 'score': sleep_score,
                  'deepPct': deep_pct or 0, 'remPct': rem_pct or 0, 'levels': [],
                  'startGMT': None, 'endGMT': None},
        'bodyBattery': {'current': body_battery, 'max': body_battery, 'charged': None, 'drained': None},
        'stress': {'avg': stress_avg, 'max': None},
        'respiration': {'avg': None, 'sleepAvg': None},
        'spo2': {'avg': None, 'min': None},
    }



@app.get('/api/health')
def health_data():
    today = date.today().isoformat()
    row = get_cache('health', uid())
    if row and (time.time() - row[1]) < 10 * 60 and has_health_payload(row[0]) and (not row[0].get('fallback') or has_sleep_levels(row[0])):
        return jsonify(row[0])

    try:
        client = get_garmin(uname())
        sleep     = safe_health_fetch('sleep', {}, lambda: client.get_sleep_data(today))
        hrv       = safe_health_fetch('hrv', {}, lambda: client.get_hrv_data(today))
        bb        = safe_health_fetch('body battery', [], lambda: client.get_body_battery(today, today))
        stress    = safe_health_fetch('stress', {}, lambda: client.get_stress_data(today))
        readiness = safe_health_fetch('training readiness', [], lambda: client.get_training_readiness(today))
        hr        = safe_health_fetch('heart rates', {}, lambda: client.get_heart_rates(today))
        resp      = safe_health_fetch('respiration', {}, lambda: client.get_respiration_data(today))
        spo2      = safe_health_fetch('spo2', {}, lambda: client.get_spo2_data(today))

        sleep = sleep if isinstance(sleep, dict) else {}
        hrv = hrv if isinstance(hrv, dict) else {}
        bb = bb if isinstance(bb, list) else []
        stress = stress if isinstance(stress, dict) else {}
        readiness = readiness if isinstance(readiness, list) else []
        hr = hr if isinstance(hr, dict) else {}
        resp = resp if isinstance(resp, dict) else {}
        spo2 = spo2 if isinstance(spo2, dict) else {}

        sleep_source_date = today
        if not (sleep.get('sleepLevels') or sleep.get('sleepMovement')):
            previous_day = (date.today() - timedelta(days=1)).isoformat()
            previous_sleep = safe_health_fetch('sleep fallback', {}, lambda: client.get_sleep_data(previous_day))
            if isinstance(previous_sleep, dict) and (previous_sleep.get('sleepLevels') or previous_sleep.get('sleepMovement')):
                sleep = previous_sleep
                sleep_source_date = previous_day

        s = sleep.get('dailySleepDTO', {})
        total_sleep_sec = s.get('sleepTimeSeconds', 0)
        deep_sec  = s.get('deepSleepSeconds', 0)
        rem_sec   = s.get('remSleepSeconds', 0)
        sleep_scores = s.get('sleepScores') or {}
        sleep_score_val = sleep_scores.get('overall', {}).get('value') if isinstance(sleep_scores, dict) else None

        hrv_sum  = hrv.get('hrvSummary', {})
        hrv_base = hrv_sum.get('baseline') or {}
        hrv_ln   = hrv_sum.get('lastNightAvg')
        hrv_wk   = hrv_sum.get('weeklyAvg')
        hrv_st   = hrv_sum.get('status')
        hrv_pct  = round((hrv_ln / hrv_wk) * 100) if hrv_wk and hrv_ln else None
        hrv_comp = hrv_component(hrv_ln, hrv_base.get('lowUpper'), hrv_base.get('balancedLow'), hrv_st, hrv_pct)
        hrv_lt, hrv_verdict = hrv_signal(hrv_st, hrv_ln, hrv_wk)

        bb_today = bb[0] if bb and isinstance(bb[0], dict) else {}
        bb_vals  = bb_today.get('bodyBatteryValuesArray') or []
        bb_points = [v[1] for v in bb_vals if v and len(v) > 1 and v[1] is not None]
        bb_now   = bb_points[-1] if bb_points else None
        bb_max   = max(bb_points, default=None)

        ready    = readiness[0] if readiness and isinstance(readiness[0], dict) else {}
        avg_resp = resp.get('avgWakingRespirationValue') or resp.get('avgRespirationValue')
        sleep_resp = resp.get('avgSleepRespirationValue')
        avg_spo2 = spo2.get('averageSpO2')
        if avg_spo2: avg_spo2 = round(avg_spo2)

        result = {
            'date': today,
            'readiness':   {'score': ready.get('score'), 'level': ready.get('level'), 'feedback': ready.get('feedbackShort')},
            'hrv':         {'lastNightAvg': hrv_ln, 'weeklyAvg': hrv_wk, 'status': hrv_st, 'pct': hrv_pct,
                            'balancedLow': hrv_base.get('balancedLow'), 'balancedUpper': hrv_base.get('balancedUpper'),
                            'lowUpper': hrv_base.get('lowUpper'), 'component': hrv_comp,
                            'light': hrv_lt, 'verdict': hrv_verdict},
            'restingHR':   {'value': hr.get('restingHeartRate'), 'sevenDayAvg': hr.get('lastSevenDaysAvgRestingHeartRate'), 'min': hr.get('minHeartRate')},
            'sleep':       {'totalSec': total_sleep_sec, 'deepSec': deep_sec, 'remSec': rem_sec, 'score': sleep_score_val,
                            'deepPct': round(deep_sec/total_sleep_sec*100) if total_sleep_sec else 0,
                            'remPct':  round(rem_sec/total_sleep_sec*100)  if total_sleep_sec else 0,
                            'levels': (sleep.get('sleepLevels') or sleep.get('sleepMovement') or []),
                            'sourceDate': sleep_source_date,
                            'fallback': sleep_source_date != today,
                            'startGMT': s.get('sleepStartTimestampGMT'),
                            'endGMT':   s.get('sleepEndTimestampGMT')},
            'bodyBattery': {'current': bb_now, 'max': bb_max, 'charged': bb_today.get('charged'), 'drained': bb_today.get('drained')},
            'stress':      {'avg': stress.get('avgStressLevel'), 'max': stress.get('maxStressLevel')},
            'respiration': {'avg': round(avg_resp) if avg_resp else None, 'sleepAvg': round(sleep_resp) if sleep_resp else None},
            'spo2':        {'avg': avg_spo2, 'min': spo2.get('lowestSpO2')},
        }
        has_payload = has_health_payload(result)
        if not has_payload:
            snapshot = latest_health_snapshot(uid(), today)
            if snapshot:
                result = snapshot
                has_payload = True
        if has_payload:
            set_cache('health', result, uid())

        # Spara även till health_history så Analysis-fliken får dagens data direkt
        try:
            if not has_payload or result.get('fallback'):
                return jsonify(result)
            sl = result['sleep']
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('''INSERT INTO health_history
                        (date, sleep_score, sleep_hours, deep_pct, rem_pct, hrv_avg, resting_hr, body_battery, stress_avg, created_at, user_id)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (date, user_id) DO UPDATE SET
                            sleep_score=EXCLUDED.sleep_score, sleep_hours=EXCLUDED.sleep_hours,
                            deep_pct=EXCLUDED.deep_pct, rem_pct=EXCLUDED.rem_pct,
                            hrv_avg=EXCLUDED.hrv_avg, resting_hr=EXCLUDED.resting_hr,
                            body_battery=EXCLUDED.body_battery, stress_avg=EXCLUDED.stress_avg''',
                        (today, sl.get('score'),
                         round(sl.get('totalSec', 0) / 3600, 2) if sl.get('totalSec') else None,
                         sl.get('deepPct'), sl.get('remPct'),
                         result['hrv'].get('lastNightAvg'),
                         result['restingHR'].get('value'),
                         result['bodyBattery'].get('max'),
                         result['stress'].get('avg'),
                         time.time(), uid()))
                conn.commit()
        except Exception:
            pass

        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.get('/api/health/spark')
def health_spark():
    """Senaste 7 dagarnas värden för hem-sidans mini-grafer (sömnpoäng, RHR, HRV)."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT sleep_score, hrv_avg, resting_hr
                FROM health_history WHERE user_id=%s ORDER BY date DESC LIMIT 7''', (uid(),))
            rows = cur.fetchall()[::-1]  # äldst först
    return jsonify({
        'sleep': [r[0] for r in rows if r[0] is not None],
        'hrv':   [r[1] for r in rows if r[1] is not None],
        'rhr':   [r[2] for r in rows if r[2] is not None],
    })

@app.get('/api/health/stress-history')
def health_stress_history():
    days = max(7, min(90, int(request.args.get('days', 30))))
    start = (date.today() - timedelta(days=days)).isoformat()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT date, stress_avg
                FROM health_history
                WHERE user_id=%s AND date >= %s AND stress_avg IS NOT NULL
                ORDER BY date
            ''', (uid(), start))
            rows = cur.fetchall()
    values = [{'date': r[0], 'value': r[1]} for r in rows]
    nums = [v['value'] for v in values if v['value'] is not None]
    avg = round(sum(nums) / len(nums), 1) if nums else None
    return jsonify({'days': days, 'avg': avg, 'values': values})


def _fetch_day_health(client, day_str):
    sleep = client.get_sleep_data(day_str) or {}
    s = sleep.get('dailySleepDTO', {}) or {}
    total = s.get('sleepTimeSeconds') or 0
    deep  = s.get('deepSleepSeconds') or 0
    rem   = s.get('remSleepSeconds') or 0
    scores = s.get('sleepScores') or {}
    sleep_score = (scores.get('overall', {}) or {}).get('value') if isinstance(scores, dict) else None
    hrv = client.get_hrv_data(day_str) or {}
    hrv_avg = (hrv.get('hrvSummary') or {}).get('lastNightAvg')
    rhr = None
    try:
        rhr = (client.get_heart_rates(day_str) or {}).get('restingHeartRate')
    except Exception:
        pass
    stress_avg = None
    try:
        stress_avg = (client.get_stress_data(day_str) or {}).get('avgStressLevel')
    except Exception:
        pass
    bb_max = None
    try:
        bb = client.get_body_battery(day_str, day_str) or []
        vals = (bb[0].get('bodyBatteryValuesArray') if bb else []) or []
        bb_max = max((v[1] for v in vals if v and v[1] is not None), default=None)
    except Exception:
        pass
    return {'date': day_str, 'sleep_score': sleep_score,
            'sleep_hours': round(total / 3600, 2) if total else None,
            'deep_pct': round(deep / total * 100) if total else None,
            'rem_pct':  round(rem / total * 100)  if total else None,
            'hrv_avg': hrv_avg, 'resting_hr': rhr, 'body_battery': bb_max,
            'stress_avg': stress_avg}


def collect_health_history(days=14, username=None):
    """Backfillar saknade dagar i health_history från Garmin (idempotent)."""
    if username is None:
        username = list(USERS.keys())[0] if USERS else 'hugo'
    user_id = USERS.get(username, {}).get('id', 1)
    try:
        client = get_garmin(username)
    except Exception as e:
        print('health-history: garmin-fel', e)
        return
    today = date.today()
    with db() as conn:
        with conn.cursor() as cur:
            # Treat a day as "have" only when newer history columns are filled too,
            # so older sparse rows get re-fetched once and backfilled.
            cur.execute('SELECT date FROM health_history WHERE user_id=%s AND body_battery IS NOT NULL AND stress_avg IS NOT NULL', (user_id,))
            have = {r[0] for r in cur.fetchall()}
    added = 0
    for i in range(1, days + 1):
        d = (today - timedelta(days=i)).isoformat()
        if d in have:
            continue
        try:
            rec = _fetch_day_health(client, d)
        except Exception as e:
            print(f'health-history {d} fel:', e)
            continue
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''INSERT INTO health_history
                    (date, sleep_score, sleep_hours, deep_pct, rem_pct, hrv_avg, resting_hr, body_battery, stress_avg, created_at, user_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (date, user_id) DO UPDATE SET sleep_score=EXCLUDED.sleep_score,
                        sleep_hours=EXCLUDED.sleep_hours, deep_pct=EXCLUDED.deep_pct,
                        rem_pct=EXCLUDED.rem_pct, hrv_avg=EXCLUDED.hrv_avg, resting_hr=EXCLUDED.resting_hr,
                        body_battery=EXCLUDED.body_battery, stress_avg=EXCLUDED.stress_avg''',
                    (rec['date'], rec['sleep_score'], rec['sleep_hours'], rec['deep_pct'],
                     rec['rem_pct'], rec['hrv_avg'], rec['resting_hr'], rec['body_battery'],
                     rec['stress_avg'], time.time(), user_id))
            conn.commit()
        added += 1
    print(f'health-history: {added} nya dagar tillagda')


# --- Fitness-mätare (VO2max, uthållighet, mjölksyratröskel, HRV-status) historik ---
def _find_num(obj, keys, depth=0):
    """Sök rekursivt efter första numeriska värdet under någon av nyckelnamnen (case-insensitive substr)."""
    if depth > 6 or obj is None:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(kk in kl for kk in keys) and isinstance(v, (int, float)) and not isinstance(v, bool):
                return v
        for v in obj.values():
            r = _find_num(v, keys, depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_num(v, keys, depth + 1)
            if r is not None:
                return r
    return None


def _fetch_day_metrics(client, day_str):
    """Hämtar fitness-mätare för en dag. Varje mätare är skyddad — saknas metoden
    (t.ex. get_lactate_threshold på garminconnect 0.3.2) hoppas den bara över."""
    vo2max = endurance = lt_hr = lt_pace = hrv_status = None
    try:
        mm = client.get_max_metrics(day_str)
        vo2max = _find_num(mm, ['vo2maxprecise', 'vo2maxvalue', 'vo2max'])
    except Exception:
        pass
    try:
        # Single-day call gives precise daily values. Passing enddate switches Garmin
        # to weekly aggregation, which can hide points in the Analysis tab.
        es = client.get_endurance_score(day_str)
        endurance = _find_num(es, ['overallscore', 'enduranceScore'.lower(), 'avg', 'gauge'])
    except Exception:
        pass
    try:
        if hasattr(client, 'get_lactate_threshold'):
            # latest=True ger den aktuella tröskeln. Daglig aggregering returnerar tomma
            # listor ({"speed": [], "heart_rate": []}) eftersom LT bara uppdateras då och då.
            lt = client.get_lactate_threshold(latest=True, start_date=day_str, end_date=day_str)
            lt_hr = _find_num(lt, ['heartrate', 'lactatethresholdheartrate'])
            speed = _find_num(lt, ['speed', 'lactatethresholdspeed'])  # m/s
            # Garmin ger löp-LT-farten 10x för liten (0.42 m/s istället för 4.22). En riktig
            # löptröskelfart ligger aldrig under ~1.5 m/s, så skala upp i så fall.
            if speed and 0 < speed < 1.5:
                speed *= 10
            if speed and speed > 0:
                lt_pace = round(1000.0 / speed, 1)  # sek/km
    except Exception:
        pass
    try:
        hrv = client.get_hrv_data(day_str) or {}
        hrv_status = (hrv.get('hrvSummary') or {}).get('status')
    except Exception:
        pass
    return {'date': day_str, 'vo2max': vo2max, 'endurance_score': int(endurance) if endurance is not None else None,
            'lactate_hr': int(lt_hr) if lt_hr is not None else None, 'lactate_pace': lt_pace,
            'hrv_status': hrv_status}


def collect_metric_history(days=45, username=None):
    """Backfillar fitness-mätare i metric_history (idempotent). Tål saknade metoder."""
    if username is None:
        username = list(USERS.keys())[0] if USERS else 'hugo'
    user_id = USERS.get(username, {}).get('id', 1)
    try:
        client = get_garmin(username)
    except Exception as e:
        print('metric-history: garmin-fel', e)
        return
    today = date.today()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT date, vo2max, endurance_score, lactate_hr, lactate_pace, hrv_status, created_at
                FROM metric_history WHERE user_id=%s''', (user_id,))
            have = {r[0]: r[1:] for r in cur.fetchall()}
    added = 0
    for i in range(0, days + 1):
        d = (today - timedelta(days=i)).isoformat()
        existing = have.get(d)
        # Revisit sparse rows created by older collectors. HRV status alone is not
        # enough for the Analysis tab's fitness trend cards.
        checked_at = existing[5] if existing else None
        recently_checked = checked_at and (time.time() - checked_at) < 20 * 3600
        if existing and any(v is not None for v in existing[:4]) and recently_checked:
            continue
        if existing and all(v is not None for v in existing[:4]):
            continue
        try:
            rec = _fetch_day_metrics(client, d)
        except Exception as e:
            print(f'metric-history {d} fel:', e)
            continue
        # Hoppa över helt tomma dagar (ingen mätare alls) så vi inte fyller tabellen med null-rader
        if not any(rec[k] is not None for k in ('vo2max', 'endurance_score', 'lactate_hr', 'lactate_pace', 'hrv_status')):
            continue
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''INSERT INTO metric_history
                    (date, vo2max, endurance_score, lactate_hr, lactate_pace, hrv_status, created_at, user_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (date, user_id) DO UPDATE SET vo2max=EXCLUDED.vo2max,
                        endurance_score=EXCLUDED.endurance_score, lactate_hr=EXCLUDED.lactate_hr,
                        lactate_pace=EXCLUDED.lactate_pace, hrv_status=EXCLUDED.hrv_status''',
                    (rec['date'], rec['vo2max'], rec['endurance_score'], rec['lactate_hr'],
                     rec['lactate_pace'], rec['hrv_status'], time.time(), user_id))
            conn.commit()
        added += 1
    print(f'metric-history: {added} nya dagar tillagda')


def _linreg_per_week(series):
    """series = lista av (dagindex_float, värde). Returnerar lutning per VECKA via minsta kvadrat."""
    n = len(series)
    if n < 2:
        return None
    sx = sum(p[0] for p in series); sy = sum(p[1] for p in series)
    sxx = sum(p[0] * p[0] for p in series); sxy = sum(p[0] * p[1] for p in series)
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    slope_per_day = (n * sxy - sx * sy) / denom
    return slope_per_day * 7.0


@app.get('/api/analysis')
def analysis():
    """Trender + förändringstakt (derivata) för fitness-mätare över ett fönster."""
    window = int(request.args.get('days', 60))
    start = (date.today() - timedelta(days=window)).isoformat()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT date, hrv_avg, resting_hr, sleep_score
                FROM health_history WHERE date >= %s AND user_id=%s ORDER BY date''', (start, uid()))
            hh = cur.fetchall()
        with conn.cursor() as cur:
            cur.execute('''SELECT date, vo2max, endurance_score, lactate_hr, lactate_pace, hrv_status
                FROM metric_history WHERE date >= %s AND user_id=%s ORDER BY date''', (start, uid()))
            mh = cur.fetchall()

    # Bygg per-mätare tidsserier (dag-index relativt fönstrets start, för lutningsberäkning)
    def to_day_index(dstr):
        return (date.fromisoformat(dstr[:10]) - date.fromisoformat(start)).days

    cols = {
        'hrv':       {'label': 'HRV',                'unit': 'ms',     'good': 'up',   'rows': hh, 'idx': 1, 'fmt': 0},
        'rhr':       {'label': 'Resting HR',         'unit': 'bpm',    'good': 'down', 'rows': hh, 'idx': 2, 'fmt': 0},
        'sleep':     {'label': 'Sleep score',        'unit': '',       'good': 'up',   'rows': hh, 'idx': 3, 'fmt': 0},
        'vo2max':    {'label': 'VO₂max',             'unit': '',       'good': 'up',   'rows': mh, 'idx': 1, 'fmt': 1},
        'endurance': {'label': 'Endurance score',    'unit': '',       'good': 'up',   'rows': mh, 'idx': 2, 'fmt': 0},
        'lt_pace':   {'label': 'Lactate threshold',  'unit': 'pace',   'good': 'down', 'rows': mh, 'idx': 4, 'fmt': 'pace'},
        'lt_hr':     {'label': 'LT heart rate',      'unit': 'bpm',    'good': 'up',   'rows': mh, 'idx': 3, 'fmt': 0},
    }

    metrics = []
    for key, c in cols.items():
        series = []
        for r in c['rows']:
            v = r[c['idx']]
            if v is None:
                continue
            series.append({'t': r[0][:10], 'v': float(v)})
        out = {'key': key, 'label': c['label'], 'unit': c['unit'], 'good': c['good'], 'fmt': c['fmt'],
               'series': series, 'latest': None, 'first': None, 'slopePerWeek': None,
               'pctChange': None, 'direction': 'unknown', 'samples': len(series)}
        if series:
            out['latest'] = series[-1]['v']
            out['first'] = series[0]['v']
            reg = [(to_day_index(p['t']), p['v']) for p in series]
            slope = _linreg_per_week(reg)
            out['slopePerWeek'] = round(slope, 3) if slope is not None else None
            if series[0]['v']:
                out['pctChange'] = round((series[-1]['v'] - series[0]['v']) / abs(series[0]['v']) * 100, 1)
            # riktning: bara stabil om <0.05% förändring per vecka (mycket snäv marginal).
            # Declines markeras alltid som declining om vi vill ha upp, och vice versa.
            mean = sum(p['v'] for p in series) / len(series)
            if slope is None or mean == 0:
                out['direction'] = 'stable'
            elif abs(slope) < abs(mean) * 0.0005:  # <0.05% per vecka = stabil
                out['direction'] = 'stable'
            else:
                rising = slope > 0
                good = (rising and c['good'] == 'up') or (not rising and c['good'] == 'down')
                out['direction'] = 'improving' if good else 'declining'
        metrics.append(out)

    latest_status = next((r[5] for r in reversed(mh) if r[5]), None)
    return jsonify({
        'window_days': window,
        'hrv_status': latest_status,
        'health_rows': len(hh),
        'metric_rows': len(mh),
        'metrics': metrics,
    })


@app.get('/api/training-load')
def training_load():
    row = get_cache('training_load', uid())
    if row and (time.time() - row[1]) < 30 * 60:
        return jsonify(row[0])
    try:
        client = get_garmin(uname())
        today  = date.today().isoformat()
        status = client.get_training_status(today)

        # Plocka ut data från primär enhet
        dev_map  = status.get('mostRecentTrainingStatus', {}).get('latestTrainingStatusData', {})
        dev      = next(iter(dev_map.values()), {}) if dev_map else {}
        acwr_dto = dev.get('acuteTrainingLoadDTO', {})

        acute   = acwr_dto.get('dailyTrainingLoadAcute')
        chronic = acwr_dto.get('dailyTrainingLoadChronic')
        ratio   = acwr_dto.get('dailyAcuteChronicWorkloadRatio')
        status_phrase = dev.get('trainingStatusFeedbackPhrase', '')

        # Belastningsbalans per månad
        lb_map  = status.get('mostRecentTrainingLoadBalance', {}).get('metricsTrainingLoadBalanceDTOMap', {})
        lb      = next(iter(lb_map.values()), {}) if lb_map else {}

        result = {
            'acute':   round(acute)   if acute   is not None else None,
            'chronic': round(chronic) if chronic is not None else None,
            'ratio':   round(ratio, 2) if ratio  is not None else None,
            'acwrStatus':   acwr_dto.get('acwrStatus'),
            'statusPhrase': status_phrase,
            'monthlyAerobicLow':  round(lb.get('monthlyLoadAerobicLow',  0)),
            'monthlyAerobicHigh': round(lb.get('monthlyLoadAerobicHigh', 0)),
            'monthlyAnaerobic':   round(lb.get('monthlyLoadAnaerobic',   0)),
            'aerobicLowMin':  lb.get('monthlyLoadAerobicLowTargetMin'),
            'aerobicLowMax':  lb.get('monthlyLoadAerobicLowTargetMax'),
            'aerobicHighMin': lb.get('monthlyLoadAerobicHighTargetMin'),
            'aerobicHighMax': lb.get('monthlyLoadAerobicHighTargetMax'),
            'anaerobicMin':   lb.get('monthlyLoadAnaerobicTargetMin'),
            'anaerobicMax':   lb.get('monthlyLoadAnaerobicTargetMax'),
            'loadBalanceFeedback': lb.get('trainingBalanceFeedbackPhrase'),
        }
        set_cache('training_load', result, uid())
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.post('/api/sync')
def sync():
    try:
        n = run_sync(username=uname(), user_id=uid())
        return jsonify({'ok': True, 'count': n})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def _get_iso_week(d):
    """Returnera ISO-veckonummer för ett date-objekt."""
    return d.isocalendar()[1]

def _build_refresh_prompt(acts):
    """Bygg en fullständig prompt för startsidans AI-rekommendation."""
    today     = date.today()
    iso_week  = _get_iso_week(today)
    weekday   = today.weekday()  # 0=mån

    # Fas baserat på vecka
    if iso_week <= 23:   phase = 'återhämtning'
    elif iso_week <= 25: phase = 'återhämtning/bas'
    elif iso_week <= 29: phase = 'basbygge'
    elif iso_week <= 33: phase = 'tröskel/tempo'
    elif iso_week <= 37: phase = 'tävlingsspecifik'
    elif iso_week <= 39: phase = 'avtrappning'
    elif iso_week <= 40: phase = 'taper'
    else:                phase = 'tävlingsvecka'

    # Planerat km per vecka (från träningsplanen)
    weekly_km_plan = {
        23:35, 24:40, 25:45,
        26:50, 27:55, 28:55, 29:58,
        30:62, 31:65, 32:65, 33:60,
        34:68, 35:70, 36:68, 37:65,
        38:55, 39:50,
        40:35, 41:15
    }
    planned_km = weekly_km_plan.get(iso_week, 40)

    # Senaste löppass med load-data
    recent_runs = [
        {'name': a.get('activityName'), 'date': a.get('startTimeLocal'),
         'distance': f"{a.get('distance',0)/1000:.1f} km",
         'duration': f"{int(a.get('duration',0)/60)} min",
         'avgHR': a.get('averageHR'),
         'trainingEffect': a.get('trainingEffectLabel'),
         'load': round(a.get('activityTrainingLoad', 0) or 0)}
        for a in acts if 'running' in (a.get('activityType', {}).get('typeKey') or '')
    ][:5]

    # Genomförd km + load denna vecka
    monday = today - timedelta(days=weekday)
    completed_km   = 0.0
    completed_load = 0.0
    for a in acts:
        raw_date = a.get('startTimeLocal') or ''
        try:
            act_date = datetime.fromisoformat(raw_date[:10]).date()
        except Exception:
            continue
        if act_date >= monday:
            completed_km   += (a.get('distance') or 0) / 1000
            completed_load += (a.get('activityTrainingLoad') or 0)

    remaining_km = max(0, planned_km - completed_km)

    # Training load (ACWR) från cache
    tl_row = get_cache('training_load', uid())
    tl     = tl_row[0] if tl_row else {}
    acute   = tl.get('acute')
    chronic = tl.get('chronic')
    ratio   = tl.get('ratio')
    acwr_status = tl.get('acwrStatus', '')
    load_feedback = tl.get('loadBalanceFeedback', '')

    # Hälsodata från cache
    h_row = get_cache('health', uid())
    h     = h_row[0] if h_row else {}
    readiness    = (h.get('readiness') or {}).get('score')
    hrv_obj      = h.get('hrv') or {}
    hrv_avg      = hrv_obj.get('lastNightAvg')
    hrv_weekly   = hrv_obj.get('weeklyAvg')
    hrv_status   = hrv_obj.get('status')
    hrv_bal_low  = hrv_obj.get('balancedLow')
    hrv_bal_high = hrv_obj.get('balancedUpper')
    hrv_comp     = hrv_obj.get('component')
    body_battery = (h.get('bodyBattery') or {}).get('current')
    sleep_score  = (h.get('sleep') or {}).get('score')

    # Google Calendar — kommande 7 dagar
    cal_row = get_cache('gcal_events', uid())
    gcal_lines = []
    early_days  = []
    if cal_row:
        for ev in (cal_row[0] or []):
            start_str = ev.get('start', '')
            if not start_str:
                continue
            try:
                ev_dt   = datetime.fromisoformat(start_str[:16])
                ev_date = ev_dt.date()
            except Exception:
                continue
            if today <= ev_date <= today + timedelta(days=14):
                day_name = ev_dt.strftime('%A') + ' ' + str(ev_dt.day) + ' ' + ev_dt.strftime('%b')
                time_str = ev_dt.strftime('%H:%M') if 'T' in start_str else 'all day'
                desc_str = f" — {ev['desc']}" if ev.get('desc') else ''
                gcal_lines.append(f"- {day_name}: {ev.get('title','')} ({time_str}){desc_str}")
                if ev_dt.hour < 7:
                    early_days.append(day_name)

    # Bygg prompten
    # Hämta dagens och nästa planerade pass från DB
    today_session = None
    next_session  = None
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT * FROM plan_sessions
                WHERE week=%s AND dow=%s AND status='planned' AND user_id=%s
                LIMIT 1""", (iso_week, weekday, uid()))
            today_session = cur.fetchone()
            cur.execute("""SELECT * FROM plan_sessions
                WHERE status='planned' AND (week > %s OR (week = %s AND dow > %s)) AND user_id=%s
                ORDER BY week, dow LIMIT 1""", (iso_week, iso_week, weekday, uid()))
            next_session = cur.fetchone()

    if today_session:
        today_km = today_session.get('km') or 0
        today_session_str = (
            f"{today_session['title']} — {today_session['detail']}"
            + (f" — {today_km:.0f} km" if today_km and str(int(today_km)) not in today_session['title'] else "")
        )
        today_km_note = f"Session distance from plan: {today_km:.0f} km — use THIS number for the session, NOT the weekly remaining km."
    else:
        today_session_str = "Rest day (no session scheduled)"
        today_km_note = ""
    next_session_str  = f"{next_session['title']} — {next_session['detail']}"   if next_session  else "No upcoming session found"

    prompt = f"""You are a personal training coach. Analyze ALL data below and respond ONLY with JSON. All text fields in the JSON must be written in Swedish (svenska).

GOAL: Half marathon under 1:20 (3:47/km) on October 10, 2026 · Current best: 1:26:19
SECONDARY GOAL: Build a strong body in all areas — running strength, upper body, core, mobility
VO2max: 59 · Plan: W23–41 · Current phase: {phase} (W{iso_week})

TODAY'S SCHEDULED SESSION (from training plan):
{today_session_str}
{today_km_note}

NEXT SCHEDULED SESSION:
{next_session_str}

RECENT RUNS:
{json.dumps(recent_runs, ensure_ascii=False, indent=2)}

WEEK STATUS W{iso_week}:
- Planned: {planned_km} km · Completed: {completed_km:.1f} km · Remaining: {remaining_km:.1f} km
- Training load this week: {round(completed_load)}

HEALTH DATA (today):
- Training readiness: {readiness or '—'}/100
- Garmin HRV Status: {hrv_status or 'NONE'} (this is Garmin's trend assessment vs your personal baseline)
- HRV last night: {hrv_avg or '—'} ms · your balanced baseline range: {hrv_bal_low or '—'}–{hrv_bal_high or '—'} ms · weekly avg: {hrv_weekly or '—'} ms
- Body battery: {body_battery or '—'}/100
- Sleep score: {sleep_score or '—'}/100"""

    # CNS-score beräkning (Flatt & Esco 2016) — HRV-komponenten bygger nu på Garmins baslinje
    if all(v is not None for v in [readiness, hrv_avg, hrv_weekly, sleep_score, h.get('stress',{}).get('avg')]):
        # Primärt: baslinje-baserad HRV-komponent. Fallback: råförhållande mot veckosnitt.
        hrv_pct_val = round((hrv_avg / hrv_weekly) * 100) if hrv_weekly else 50
        hrv_score   = hrv_comp if hrv_comp is not None else min(hrv_pct_val, 100)
        stress_avg  = h.get('stress', {}).get('avg', 50) or 50
        cns = round(0.40 * hrv_score + 0.30 * (sleep_score or 50) + 0.20 * (readiness or 50) + 0.10 * (100 - min(stress_avg,100)))
        st = (hrv_status or 'NONE').upper()
        hrv_signal_str = {'BALANCED':'GREEN (balanced — train as planned)',
                          'UNBALANCED':'YELLOW (HRV i obalans — caution)',
                          'LOW':'RED (low — recover)',
                          'POOR':'RED (poor — rest)'}.get(st)
        if not hrv_signal_str:
            hrv_diff = ((hrv_avg - hrv_weekly) / hrv_weekly * 100) if hrv_weekly else 0
            hrv_signal_str = 'GREEN (go hard)' if hrv_diff >= 5 else 'RED (rest/Z2)' if hrv_diff <= -5 else 'YELLOW (normal session)'
        cns_rule = 'QUALITY SESSION OK' if cns >= 70 else 'NORMAL/EASY SESSION' if cns >= 45 else 'REST OR Z2 — mandatory'
        deep_pct = h.get('sleep', {}).get('deepPct', 0) or 0
        rem_pct  = h.get('sleep', {}).get('remPct', 0) or 0
        sleep_flags = []
        if deep_pct < 10: sleep_flags.append('low deep sleep (skip strength)')
        if rem_pct < 15:  sleep_flags.append('low REM (avoid intervals)')
        prompt += f"""

CNS SCORE: {cns}/100 — {cns_rule}
HRV SIGNAL (Garmin Status): {hrv_signal_str}
SLEEP QUALITY: deep sleep {deep_pct}% (goal 15–25%) · REM {rem_pct}% (goal 20–25%){(' · WARNING: ' + ', '.join(sleep_flags)) if sleep_flags else ' · OK'}
SESSION RULE: CNS ≥70 → quality session ok · CNS 45–69 → normal/easy · CNS <45 → rest/Z2 mandatory"""

    if acute is not None:
        load_feedback_en = {
            'AEROBIC_LOW_SHORTAGE':  'too little low-intensity aerobic training',
            'AEROBIC_HIGH_SHORTAGE': 'too little high-intensity aerobic training',
            'ANAEROBIC_SHORTAGE':    'too little anaerobic training',
            'OPTIMAL':               'optimal balance',
        }.get(load_feedback, load_feedback)
        acwr_en = {'LOW':'low','OPTIMAL':'optimal','HIGH':'high','VERY_HIGH':'very high'}.get(acwr_status, acwr_status)
        prompt += f"""

TRAINING LOAD (ACWR):
- Acute load (7 days): {acute} · Chronic load (28 days): {chronic}
- ACWR ratio: {ratio} ({acwr_en}) — optimal zone is 0.8–1.3
- Load balance: {load_feedback_en}
RULE: If ACWR < 0.8 you can carefully increase intensity. If > 1.3, prioritize rest or Z2."""

    if gcal_lines:
        prompt += f"""

CALENDAR (next 7 days):
{chr(10).join(gcal_lines)}
Factor this into the recommendation — avoid hard sessions on heavy work days."""

    if early_days:
        prompt += f"\nEarly starts (before 07:00, likely reduced sleep): {', '.join(early_days)} — avoid quality sessions on these days and the day after."

    prompt += """

Respond ONLY with this JSON (no explanation outside JSON):
{
  "todayRecommendation": "1-2 sentence recommendation for today that references the scheduled session above — confirm it, modify it, or replace it based on the health data",
  "todayType": "easy|quality|rest",
  "nextSession": {"title": "session name", "desc": "description", "tempo": "e.g. 3:35 /km", "distance": "e.g. ~8 km"},
  "prediction3k": "e.g. 10:15",
  "insight": "one concrete insight based on training load or health data"
}"""
    return prompt

@app.post('/api/refresh')
def refresh():
    row = get_cache('analysis', uid())
    if row and (time.time() - row[1]) < 60 * 60:
        return jsonify(row[0])

    try:
        client = get_garmin(uname())
        acts = client.get_activities(0, 10)
        save_activities(acts, uid())
    except Exception as e:
        return jsonify({'error': 'Garmin error: ' + str(e)}), 500

    if not ANTHROPIC_KEY or ANTHROPIC_KEY.startswith('sk-ant-placeholder'):
        return jsonify({'todayRecommendation': 'Add an Anthropic API key in .env.',
                        'todayType': 'easy',
                        'nextSession': {'title': 'Easy jog', 'desc': 'Z2, 30-40 min', 'tempo': '4:45-5:15 /km', 'distance': '~6 km'},
                        'prediction3k': '10:27', 'insight': 'AI insights require an API key.'})

    prompt = _build_refresh_prompt(acts)
    resp = requests.post('https://api.anthropic.com/v1/messages',
        json={'model': 'claude-sonnet-4-6', 'max_tokens': 600,
              'messages': [{'role': 'user', 'content': prompt}]},
        headers={'x-api-key': ANTHROPIC_KEY, 'anthropic-version': '2023-06-01',
                 'content-type': 'application/json'})

    text = resp.json()['content'][0]['text'].strip().replace('```json','').replace('```','').strip()
    analysis = json.loads(text)
    set_cache('analysis', analysis, uid())
    return jsonify(analysis)

# ─────────────────────────────────────────────
# AI-ANALYS AV SENASTE PASSEN (planerat vs faktiskt)
# ─────────────────────────────────────────────
def _build_review_prompt():
    """Prompt för AI-koll på DAGENS pass: planerat vs gjort, med tidsmedvetenhet."""
    now   = datetime.now()
    today = now.date()
    wk, dw = _iso_week_dow(today)

    # Dagens planerade pass + dagens faktiska aktiviteter
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('SELECT * FROM plan_sessions WHERE week=%s AND dow=%s AND user_id=%s', (wk, dw, uid()))
            planned = cur.fetchall()
        with conn.cursor() as cur:
            cur.execute('''SELECT id, name, type, distance, duration, avg_hr
                FROM activities WHERE date >= %s AND user_id=%s ORDER BY date''', (today.isoformat(), uid()))
            act_rows = cur.fetchall()

    planned_str = '; '.join(f"{p['title']} — {p['detail']}" for p in planned) if planned \
                  else 'Rest day (no session scheduled)'

    INTERVAL_TYPES = {'track_running', 'interval_training', 'track'}

    def _fmt_pace(speed_ms):
        """Convert m/s to mm:ss/km string."""
        if not speed_ms or speed_ms <= 0:
            return None
        pace = 1000 / speed_ms / 60  # min/km
        return f"{int(pace)}:{int((pace % 1) * 60):02d}/km"

    def _fetch_laps(activity_id):
        """Return work-interval laps for an activity, filtering out rest laps."""
        try:
            client = get_garmin(uname())
            splits = client.get_activity_splits(activity_id)
            laps = splits.get('lapDTOs') or splits.get('laps') or []
            if not laps:
                return []
            # Compute pace for each lap
            lap_data = []
            for lap in laps:
                spd = lap.get('averageSpeed') or lap.get('avgSpeed')
                dist = lap.get('distance') or 0
                dur  = lap.get('duration') or lap.get('elapsedDuration') or 0
                hr   = lap.get('averageHR') or lap.get('avgHR')
                if dist < 50:   # skip sub-50 m auto-laps / pauses
                    continue
                lap_data.append({'dist': dist, 'dur': dur, 'speed': spd, 'hr': hr})
            if not lap_data:
                return []
            # Identify work laps as the fastest half (by speed)
            speeds = sorted([l['speed'] for l in lap_data if l['speed']], reverse=True)
            if not speeds:
                return lap_data  # no speed data — return all
            threshold = speeds[max(0, len(speeds) // 2 - 1)]  # median speed of top half
            return [l for l in lap_data if l['speed'] and l['speed'] >= threshold]
        except Exception:
            return []

    acts = []
    lap_notes = []
    for act_id, name, typ, dist, dur, hr in act_rows:
        is_interval = (typ or '').lower() in INTERVAL_TYPES or \
                      any(w in (name or '').lower() for w in ('interval', 'track', 'fartlek', 'repeat'))
        parts = [typ or 'activity']
        if dist: parts.append(f"{dist/1000:.1f} km")
        if dur:  parts.append(f"{int(dur/60)} min")
        if dist and dur and dist > 0:
            pace = (dur / 60) / (dist / 1000)
            pace_note = ' (avg incl. rest)' if is_interval else ''
            parts.append(f"pace {int(pace)}:{int((pace % 1) * 60):02d}/km{pace_note}")
        if hr: parts.append(f"avgHR {hr}")
        acts.append(f"{name or 'Activity'} ({', '.join(parts)})")

        if is_interval and act_id:
            work_laps = _fetch_laps(act_id)
            if work_laps:
                lap_lines = []
                for i, l in enumerate(work_laps, 1):
                    p = _fmt_pace(l['speed'])
                    d = f"{l['dist']:.0f} m"
                    h_str = f", HR {l['hr']}" if l['hr'] else ''
                    lap_lines.append(f"  Rep {i}: {d} @ {p or '?'}{h_str}")
                lap_notes.append(
                    f"INTERVAL REPS for '{name or 'track activity'}' "
                    f"(work laps only, rest excluded):\n" + '\n'.join(lap_lines)
                )

    acts_str = '; '.join(acts) if acts else 'nothing logged yet today'
    if lap_notes:
        acts_str += '\n\n' + '\n\n'.join(lap_notes)
        acts_str += ('\n\nNOTE: Use the rep paces above (not the average pace) when evaluating '
                     'interval performance against the target pace in the plan.')

    # Dagens kalender (jobb/åtaganden) så "har du tid" blir smart
    cal_row = get_cache('gcal_events', uid())
    today_events = []
    if cal_row:
        for ev in (cal_row[0] or []):
            s = ev.get('start', '')
            if s[:10] != today.isoformat():
                continue
            t  = s[11:16] if 'T' in s else 'all day'
            e  = ev.get('end', '')
            te = e[11:16] if 'T' in e else ''
            today_events.append(f"{ev.get('title','')} ({t}{'–' + te if te else ''})")
    events_str = '; '.join(today_events) if today_events else 'nothing on the calendar'

    return f"""You are a personal running coach. Look ONLY at TODAY and tell the athlete how today's planned session is going right now.

GOAL: Half marathon under 1:20 on October 10, 2026.
Current date & time: {now.strftime('%A %d %b, %H:%M')}

TODAY'S PLANNED SESSION:
{planned_str}

ACTIVITIES LOGGED TODAY (from Garmin):
{acts_str}

TODAY'S CALENDAR (work / commitments):
{events_str}

Decide which single case applies and write accordingly:
- DONE: an activity matching the planned session was completed today. Praise it. For interval/track sessions, use the individual REP PACES listed above (not the average pace) to compare against the target pace in the plan.
- PENDING: the session has not been done yet. Use the current time AND the calendar to judge if there is still time today — if so, reassure ("you still have time, fit it in before/after work"); if it's late evening with no window left, gently note the day is nearly over.
- OTHER: the athlete did something different than planned today — acknowledge it.
- REST: it's a rest day — confirm that resting is the right call.

Respond ONLY with this JSON (all text in Swedish / svenska):
{{
  "status": "done | pending | missed | rest | other",
  "headline": "max 6 words",
  "body": "1-3 short, friendly sentences specific to today."
}}"""

@app.get('/api/training-review')
def training_review():
    force = request.args.get('force') == '1'
    row = get_cache('training_review', uid())
    if row and not force and (time.time() - row[1]) < 30 * 60:
        return jsonify(row[0])
    if not ANTHROPIC_KEY or ANTHROPIC_KEY.startswith('sk-ant-placeholder'):
        return jsonify({'status': 'pending', 'headline': 'AI key required',
                        'body': 'Add an Anthropic API key in .env to enable today\'s session check.'})
    try:
        prompt = _build_review_prompt()
        resp = requests.post('https://api.anthropic.com/v1/messages',
            json={'model': 'claude-sonnet-4-6', 'max_tokens': 500,
                  'messages': [{'role': 'user', 'content': prompt}]},
            headers={'x-api-key': ANTHROPIC_KEY, 'anthropic-version': '2023-06-01',
                     'content-type': 'application/json'})
        text = resp.json()['content'][0]['text'].strip().replace('```json','').replace('```','').strip()
        review = json.loads(text)
        set_cache('training_review', review, uid())
        return jsonify(review)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def _build_insights_prompt():
    today = date.today()
    start = (today - timedelta(days=21)).isoformat()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT date, sleep_score, sleep_hours, deep_pct, rem_pct, hrv_avg, resting_hr
                FROM health_history WHERE date >= %s AND user_id=%s ORDER BY date''', (start, uid()))
            hh = cur.fetchall()
        with conn.cursor() as cur:
            cur.execute('''SELECT date, type, distance FROM activities WHERE date >= %s AND user_id=%s ORDER BY date''', (start, uid()))
            acts = cur.fetchall()
        with conn.cursor() as cur:
            cur.execute('SELECT text, category FROM user_notes WHERE user_id=%s ORDER BY created_at DESC LIMIT 25', (uid(),))
            notes = cur.fetchall()

    acts_by_day = {}
    for d, typ, dist in acts:
        key = (d or '')[:10]
        label = (typ or 'activity') + (f" {dist/1000:.1f}km" if dist else '')
        acts_by_day.setdefault(key, []).append(label)

    cal_row = get_cache('gcal_events', uid())
    cal_days = {}
    if cal_row:
        for ev in (cal_row[0] or []):
            s = ev.get('start', '')
            key = s[:10]
            if not key:
                continue
            title = ev.get('title', 'event')
            early = ('T' in s and s[11:13].isdigit() and int(s[11:13]) < 7)
            prefix = 'early ' if early else ''
            cal_days.setdefault(key, []).append(f"{prefix}{title}")

    lines = []
    for d, ss, sh, dp, rp, hv, rhr in hh:
        key = d[:10]
        tr = ', '.join(acts_by_day.get(key, [])) or 'rest/none'
        cal_str = '; '.join(cal_days.get(key, [])) or '-'
        lines.append(f"{key}: sleep {ss if ss is not None else '-'} ({sh if sh is not None else '-'}h, "
                     f"deep {dp if dp is not None else '-'}%, REM {rp if rp is not None else '-'}%), "
                     f"HRV {hv if hv is not None else '-'}, RHR {rhr if rhr is not None else '-'} | "
                     f"training: {tr} | calendar: {cal_str}")
    log = '\n'.join(lines) if lines else 'No history collected yet.'
    notes_txt = '\n'.join(f"- [{c}] {t}" for t, c in notes) if notes else 'None'

    temp_note = ''
    try:
        r = requests.get(f'{AC_KEEPER_URL}/api/control-events', params={'hours': 24}, timeout=4)
        tps = [e['measured_c'] for e in r.json() if e.get('measured_c') is not None]
        if tps:
            temp_note = (f"\nBEDROOM TEMP (last 24h): avg {sum(tps)/len(tps):.1f}°C, "
                         f"range {min(tps):.1f}-{max(tps):.1f}°C (longer history builds over time).")
    except Exception:
        pass

    return f"""You are a brutal, data-driven performance analyst like WHOOP. 3 weeks of data below. Surface the 3-4 most important patterns — ONLY what the numbers support.

GOAL: Half marathon sub 1:20, October 10 2026.

DATA (date: sleep score, hours, deep%, REM%, HRV, RHR | training | calendar):
{log}
{temp_note}

NOTES: {notes_txt}

Rules:
- title: max 4 words, punchy
- value: the key number (e.g. "−8 ms HRV", "+45 min sleep", "RHR 52→58")
- detail: exactly ONE sentence, max 12 words, cite the actual number
- action: max 5 words, starts with a verb
- icon: one emoji that fits the category (sleep=😴, HRV=💙, training=🏃, fatigue=⚠️, trend=📈, calendar=📅, temp=🌡️)
- color: "green", "amber", or "red" based on whether this is positive/neutral/negative

Write ALL text fields (headline, title, value, detail, action) in Swedish (svenska).
Respond ONLY with this JSON:
{{
  "headline": "max 5 words",
  "status": "good | watch | caution",
  "insights": [
    {{"icon": "emoji", "title": "max 4 words", "value": "short metric", "detail": "one sentence max 12 words", "action": "max 5 words", "color": "green|amber|red"}}
  ]
}}
3-4 insights, most impactful first. Only patterns the data clearly supports."""


@app.get('/api/insights')
def insights():
    force = request.args.get('force') == '1'
    try:
        row = get_cache('insights', uid())
        if row and not force and (time.time() - row[1]) < 12 * 3600:
            return jsonify(row[0])
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM health_history WHERE user_id=%s', (uid(),))
                n = cur.fetchone()[0]
    except Exception as e:
        print('insights db error:', e)
        return jsonify({'error': 'Database error: ' + str(e)}), 500

    if n < 3:
        return jsonify({'status': 'watch', 'headline': 'Gathering your data…',
                        'insights': [{'title': 'Building history',
                                      'detail': f'Collected {n} day(s) so far. Insights sharpen as more sleep/HRV/training history accumulates.',
                                      'action': 'Check back soon — history backfills automatically.'}]})
    if not ANTHROPIC_KEY or ANTHROPIC_KEY.startswith('sk-ant-placeholder'):
        return jsonify({'status': 'watch', 'headline': 'AI key required',
                        'insights': [{'title': 'No API key', 'detail': 'Add ANTHROPIC_API_KEY to .env to enable AI insights.', 'action': ''}]})
    try:
        prompt = _build_insights_prompt()
        resp = requests.post('https://api.anthropic.com/v1/messages',
            json={'model': 'claude-sonnet-4-6', 'max_tokens': 2000,
                  'messages': [{'role': 'user', 'content': prompt}]},
            headers={'x-api-key': ANTHROPIC_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
        rj = resp.json()
        if 'error' in rj:
            err_msg = rj['error'].get('message', str(rj['error']))
            print('insights anthropic error:', err_msg)
            return jsonify({'error': 'Anthropic API error: ' + err_msg}), 500
        text = rj['content'][0]['text'].strip().replace('```json', '').replace('```', '').strip()
        data = json.loads(text)
        set_cache('insights', data, uid())
        return jsonify(data)
    except Exception as e:
        print('insights error:', e)
        return jsonify({'error': str(e)}), 500

def _build_sleep_insights_prompt():
    today = date.today()
    start = (today - timedelta(days=28)).isoformat()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT date, sleep_score, sleep_hours, deep_pct, rem_pct, hrv_avg, resting_hr
                FROM health_history WHERE date >= %s AND user_id=%s ORDER BY date''', (start, uid()))
            hh = cur.fetchall()
        with conn.cursor() as cur:
            cur.execute('''SELECT date, type, distance FROM activities WHERE date >= %s AND user_id=%s ORDER BY date''', (start, uid()))
            acts = cur.fetchall()

    acts_by_day = {}
    for d, typ, dist in acts:
        key = (d or '')[:10]
        label = (typ or 'activity') + (f' {dist/1000:.1f}km' if dist else '')
        acts_by_day.setdefault(key, []).append(label)

    cal_row = get_cache('gcal_events', uid())
    cal_by_day = {}
    if cal_row:
        for ev in (cal_row[0] or []):
            s = ev.get('start', '')
            key = s[:10]
            if not key: continue
            title = ev.get('title', 'event')
            early = 'T' in s and s[11:13].isdigit() and int(s[11:13]) < 7
            cal_by_day.setdefault(key, []).append(('early ' if early else '') + title)

    lines = []
    for d, ss, sh, dp, rp, hv, rhr in hh:
        key = d[:10]
        tr  = ', '.join(acts_by_day.get(key, [])) or 'rest'
        cal = '; '.join(cal_by_day.get(key, [])) or '-'
        lines.append(f"{key}: score={ss} hours={sh} deep={dp}% REM={rp}% HRV={hv} RHR={rhr} | training: {tr} | calendar: {cal}")
    log = '\n'.join(lines) if lines else 'No history yet.'

    temp_note = ''
    try:
        r = requests.get(f'{AC_KEEPER_URL}/api/control-events', params={'hours': 168}, timeout=4)
        events = r.json()
        if events:
            by_day = {}
            for e in events:
                if e.get('measured_c') is None: continue
                day = e.get('timestamp', '')[:10]
                by_day.setdefault(day, []).append(e['measured_c'])
            daily_temps = {d: round(sum(v)/len(v), 1) for d, v in by_day.items()}
            temp_lines = [f"{d}: avg {t}°C" for d, t in sorted(daily_temps.items())]
            temp_note = '\nBEDROOM TEMPERATURE (last 7 nights):\n' + '\n'.join(temp_lines)
    except Exception:
        pass

    return f"""You are a blunt sleep coach. Analyze 4 weeks of sleep data. Find the 3-4 most important patterns — only what numbers actually show. Write all output (headline, title, value, detail, action) in Swedish (svenska).

DATA (date: sleep score, hours, deep%, REM%, HRV, RHR | training | calendar):
{log}
{temp_note}

Rules:
- title: max 4 words, punchy (e.g. "Late REM kicks in", "Work kills deep sleep")
- value: the key number (e.g. "avg 6h 40m", "deep 12%", "wake 07:15")
- detail: ONE sentence, max 12 words, cite actual numbers or dates
- action: max 5 words, starts with a verb, specific to tonight/this week
- icon: one emoji (😴=sleep duration, 🔵=deep sleep, 🟣=REM, ⏰=wake time, 🌡️=temp, 🏃=training effect, 📅=schedule)
- color: "green" if positive pattern, "amber" if watch, "red" if problem

Write ALL text fields (headline, title, value, detail, action) in Swedish (svenska).
Respond ONLY with this JSON:
{{
  "headline": "max 5 words, describes their sleep pattern",
  "status": "good | watch | caution",
  "insights": [
    {{"icon": "emoji", "title": "max 4 words", "value": "short metric", "detail": "one sentence max 12 words", "action": "max 5 words", "color": "green|amber|red"}}
  ]
}}
3-4 insights, most impactful first."""


@app.get('/api/sleep-insights')
def sleep_insights():
    force = request.args.get('force') == '1'
    try:
        row = get_cache('sleep_insights', uid())
        if row and not force and (time.time() - row[1]) < 12 * 3600:
            return jsonify(row[0])
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM health_history WHERE user_id=%s', (uid(),))
                n = cur.fetchone()[0]
    except Exception as e:
        return jsonify({'error': 'Database error: ' + str(e)}), 500

    if n < 5:
        return jsonify({'status': 'watch', 'headline': 'Collecting sleep data…',
                        'insights': [{'title': 'Need more history',
                                      'detail': f'Have {n} night(s) so far — need at least 5 to find patterns.',
                                      'action': 'Check back in a few days.'}]})
    if not ANTHROPIC_KEY or ANTHROPIC_KEY.startswith('sk-ant-placeholder'):
        return jsonify({'status': 'watch', 'headline': 'AI key required',
                        'insights': [{'title': 'No API key', 'detail': 'Add ANTHROPIC_API_KEY to .env.', 'action': ''}]})
    try:
        prompt = _build_sleep_insights_prompt()
        resp = requests.post('https://api.anthropic.com/v1/messages',
            json={'model': 'claude-sonnet-4-6', 'max_tokens': 2000,
                  'messages': [{'role': 'user', 'content': prompt}]},
            headers={'x-api-key': ANTHROPIC_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
        rj = resp.json()
        if 'error' in rj:
            return jsonify({'error': 'Anthropic: ' + rj['error'].get('message', str(rj['error']))}), 500
        text = rj['content'][0]['text'].strip().replace('```json', '').replace('```', '').strip()
        data = json.loads(text)
        set_cache('sleep_insights', data, uid())
        return jsonify(data)
    except Exception as e:
        print('sleep_insights error:', e)
        return jsonify({'error': str(e)}), 500


def _parse_calendar_dt(value):
    if not value:
        return None
    try:
        if 'T' not in value:
            return datetime.fromisoformat(value).replace(tzinfo=LOCAL_TZ)
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        return dt.astimezone(LOCAL_TZ)
    except Exception:
        return None


def _fmt_clock(dt):
    return dt.strftime('%H:%M')


def _event_kind(title):
    t = (title or '').lower()
    work_words = ('work', 'jobb', 'jobba', 'meeting', 'möte', 'shift', 'pass', 'office')
    travel_words = ('flight', 'flyg', 'train', 'tåg', 'airport', 'resa', 'travel')
    if any(w in t for w in travel_words):
        return 'travel'
    if any(w in t for w in work_words):
        return 'work'
    return 'calendar'


@app.get('/api/sleep-coach')
def sleep_coach():
    """Sömncoach: bygg kommande sömnschema från kalender + senaste sömn."""
    """Build one practical recommendation for tonight from sleep history + tomorrow calendar."""
    target_base_h = 7.5
    today = date.today()

    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT date, sleep_score, sleep_hours, hrv_avg, resting_hr
                    FROM health_history WHERE user_id=%s ORDER BY date DESC LIMIT 7''', (uid(),))
                history = cur.fetchall()
    except Exception as e:
        return jsonify({'error': 'Database error: ' + str(e)}), 500

    recent_hours = [float(r[2]) for r in history if r[2] is not None]
    avg_sleep = round(sum(recent_hours) / len(recent_hours), 2) if recent_hours else None
    last_sleep = recent_hours[0] if recent_hours else None
    sleep_score = history[0][1] if history and history[0][1] is not None else None

    sleep_debt = max(0, target_base_h - (last_sleep or target_base_h))
    target_h = target_base_h
    if sleep_debt >= 1.25 or (sleep_score is not None and sleep_score < 60):
        target_h = 8.5
    elif sleep_debt >= 0.5 or (sleep_score is not None and sleep_score < 75):
        target_h = 8.0

    cal_row = get_cache('gcal_events', uid())
    events = cal_row[0] if cal_row else []
    event_starts = []
    for ev in events or []:
        if ev.get('allDay'):
            continue
        start = _parse_calendar_dt(ev.get('start'))
        if not start:
            continue
        event_starts.append({
            'title': ev.get('title', 'Calendar event'),
            'start': start,
            'kind': _event_kind(ev.get('title', '')),
            'location': ev.get('location', ''),
        })

    wake_day = today + timedelta(days=1)
    day_events = [e for e in event_starts if e['start'].date() == wake_day]
    weekend = wake_day.weekday() >= 5
    default_wake = datetime.combine(wake_day, datetime.min.time(), LOCAL_TZ).replace(
        hour=8 if weekend else 7, minute=30 if weekend else 0
    )

    chosen_event = None
    wake_dt = default_wake
    anchor = None
    reason = 'Normal vakentid imorgon'
    for ev in sorted(day_events, key=lambda e: e['start']):
        buffer_min = 75
        if ev['kind'] == 'travel':
            buffer_min = 120
        elif ev['kind'] == 'work':
            buffer_min = 90
        candidate_wake = ev['start'] - timedelta(minutes=buffer_min)
        if candidate_wake < default_wake:
            chosen_event = ev
            wake_dt = max(candidate_wake, datetime.combine(wake_day, datetime.min.time(), LOCAL_TZ).replace(hour=5))
            break

    if chosen_event:
        anchor = {
            'title': chosen_event['title'],
            'time': _fmt_clock(chosen_event['start']),
            'kind': chosen_event['kind'],
        }
        reason = f"{chosen_event['title']} börjar {_fmt_clock(chosen_event['start'])}, så vakna tidigare."

    bedtime = wake_dt - timedelta(hours=target_h)
    wind_down = bedtime - timedelta(minutes=45)
    ac_precool = bedtime - timedelta(hours=2)

    night = {
        'date': wake_day.isoformat(),
        'label': wake_day.strftime('%a %d %b'),
        'bedtime': _fmt_clock(bedtime),
        'wake': _fmt_clock(wake_dt),
        'windDown': _fmt_clock(wind_down),
        'acPrecool': _fmt_clock(ac_precool),
        'targetHours': target_h,
        'reason': reason,
        'anchor': anchor,
    }

    headline = 'Lägg dig ' + night['bedtime']
    if anchor:
        headline = 'Kalenderanpassad sömn'
    elif sleep_debt >= 0.5:
        headline = 'Ta igen sömnskuld'

    reason_bits = []
    if last_sleep is not None:
        reason_bits.append(f"i natt blev {last_sleep:.1f}h")
    if sleep_score is not None:
        reason_bits.append(f"sömnpoäng {sleep_score}")
    if anchor:
        reason_bits.append(f"imorgon börjar med {anchor['title']} kl {anchor['time']}")
    basis = ', '.join(reason_bits) if reason_bits else 'din normala vakentid'

    return jsonify({
        'ok': True,
        'headline': headline,
        'targetHours': target_h,
        'avgSleepHours': avg_sleep,
        'lastSleepHours': last_sleep,
        'sleepScore': sleep_score,
        'calendarSynced': bool(cal_row),
        'summary': (
            f"Lägg dig {night['bedtime']} i natt för att få cirka {target_h:g}h sömn. "
            f"Detta baseras på {basis}."
        ),
        'night': night,
        'nights': [night],
    })


@app.post('/api/chat')
def chat():
    data = request.json or {}
    if not ANTHROPIC_KEY:
        return jsonify({'reply': 'API key missing.'})
    resp = requests.post('https://api.anthropic.com/v1/messages',
        json={'model': 'claude-sonnet-4-6', 'max_tokens': 1024,
              'system': data.get('context', 'You are a personal training coach. Always respond in Swedish (svenska).'),
              'messages': [{'role': 'user', 'content': data.get('message', '')}]},
        headers={'x-api-key': ANTHROPIC_KEY, 'anthropic-version': '2023-06-01',
                 'content-type': 'application/json'})
    return jsonify({'reply': resp.json()['content'][0]['text']})

# --- Google Calendar ---
def get_gcal_service():
    if not GCAL_AVAILABLE:
        return None
    if not os.path.exists(GCAL_CREDS):
        return None
    token_path = gcal_token()
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, GCAL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(GRequest())
            except Exception as ex:
                # Refresh-token utgången/återkallad (Google "Testing"-appar: 7 dagar).
                # Kasta inte 500 — kräver ny inloggning via reauth_google.py.
                print('Google token-refresh misslyckades, ny inloggning krävs:', ex)
                return None
        else:
            flow = InstalledAppFlow.from_client_secrets_file(GCAL_CREDS, GCAL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return gbuild('calendar', 'v3', credentials=creds)

def fetch_gcal_events(days=14, past_days=30):
    svc = get_gcal_service()
    if not svc:
        return []
    now = datetime.utcnow()
    time_min = (now - timedelta(days=past_days)).isoformat() + 'Z'
    time_max = (now + timedelta(days=days)).isoformat() + 'Z'
    try:
        result = svc.events().list(
            calendarId=GCAL_ID,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=200,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = []
        for e in result.get('items', []):
            start = e['start'].get('dateTime', e['start'].get('date', ''))
            end   = e['end'].get('dateTime',   e['end'].get('date', ''))
            events.append({
                'id':       e.get('id'),
                'title':    e.get('summary', 'Event'),
                'start':    start,
                'end':      end,
                'allDay':   'dateTime' not in e['start'],
                'location': e.get('location', ''),
                'desc':     e.get('description', ''),
            })
        return events
    except Exception as ex:
        print('Google Calendar fel:', ex)
        return []

@app.get('/api/calendar')
def calendar_events():
    if not os.path.exists(GCAL_CREDS):
        return jsonify({'ok': False, 'error': 'google_credentials.json is missing', 'events': []})
    if get_gcal_service() is None:
        return jsonify({'ok': False, 'error': 'Google token has expired or been revoked. Run reauth_google.py and sign in again.', 'events': []})
    events = fetch_gcal_events(days=90, past_days=30)
    # Cacha i DB i 30 min
    set_cache('gcal_events', events, uid())
    return jsonify({'ok': True, 'events': events})

@app.get('/api/calendar/status')
def calendar_status():
    has_creds = os.path.exists(GCAL_CREDS)
    has_token = os.path.exists(gcal_token())
    return jsonify({'hasCreds': has_creds, 'hasToken': has_token, 'available': GCAL_AVAILABLE})

# --- Minne / Noteringar ---
@app.get('/api/notes')
def get_notes():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id, text, category, created_at FROM user_notes WHERE user_id=%s ORDER BY created_at DESC', (uid(),))
            rows = cur.fetchall()
    return jsonify({'notes': [{'id': r[0], 'text': r[1], 'category': r[2], 'created_at': r[3]} for r in rows]})

@app.post('/api/notes')
def add_note():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get('text', '').strip()
    category = data.get('category', 'general')
    if not text:
        return jsonify({'error': 'Empty note'}), 400
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO user_notes (text, category, created_at, user_id) VALUES (%s, %s, %s, %s) RETURNING id',
                        (text, category, time.time(), uid()))
            new_id = cur.fetchone()[0]
        conn.commit()
    return jsonify({'ok': True, 'id': new_id})

@app.delete('/api/notes/<int:note_id>')
def delete_note(note_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM user_notes WHERE id=%s AND user_id=%s', (note_id, uid()))
        conn.commit()
    return jsonify({'ok': True})

# --- Dagbok ---
@app.get('/api/journal')
def get_journal():
    try:
        limit = min(int(request.args.get('limit', 30)), 90)
    except ValueError:
        limit = 30
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT id, entry_date, mood, energy, text, created_at, updated_at
                FROM journal_entries
                WHERE user_id=%s
                ORDER BY entry_date DESC
                LIMIT %s
            ''', (uid(), limit))
            rows = cur.fetchall()
    return jsonify({'entries': [
        {
            'id': r[0],
            'date': r[1],
            'mood': r[2] or '',
            'energy': r[3],
            'text': r[4],
            'created_at': r[5],
            'updated_at': r[6],
        } for r in rows
    ]})

@app.post('/api/journal')
def save_journal():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get('text', '').strip()
    entry_date = (data.get('date') or datetime.now(LOCAL_TZ).date().isoformat()).strip()
    mood = (data.get('mood') or '').strip()[:32]
    energy = data.get('energy')
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', entry_date):
        return jsonify({'error': 'Invalid date'}), 400
    if not text:
        return jsonify({'error': 'Empty journal entry'}), 400
    try:
        energy = int(energy) if energy not in (None, '') else None
    except (TypeError, ValueError):
        energy = None
    if energy is not None:
        energy = max(1, min(5, energy))
    now = time.time()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                INSERT INTO journal_entries (entry_date, mood, energy, text, created_at, updated_at, user_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (entry_date, user_id)
                DO UPDATE SET mood=EXCLUDED.mood, energy=EXCLUDED.energy, text=EXCLUDED.text, updated_at=EXCLUDED.updated_at
                RETURNING id
            ''', (entry_date, mood, energy, text, now, now, uid()))
            entry_id = cur.fetchone()[0]
        conn.commit()
    return jsonify({'ok': True, 'id': entry_id})

@app.delete('/api/journal/<int:entry_id>')
def delete_journal(entry_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM journal_entries WHERE id=%s AND user_id=%s', (entry_id, uid()))
        conn.commit()
    return jsonify({'ok': True})

# --- Styrka ---
STRENGTH_TYPES = ('strength_training', 'fitness_equipment', 'gym', 'indoor_cardio', 'cardio', 'bouldering')

@app.get('/api/strength')
def strength_sessions():
    try:
        link_manual_exercises_to_activities()
    except Exception as e:
        print('Strength-länkning fel:', e)
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT raw FROM activities WHERE type = ANY(%s) AND user_id=%s ORDER BY date DESC LIMIT 30",
                        (list(STRENGTH_TYPES), uid()))
            rows = cur.fetchall()
    sessions = []
    for r in rows:
        a = r[0]
        sessions.append({
            'id': str(a.get('activityId')),
            'name': a.get('activityName', 'Strength session'),
            'date': a.get('startTimeLocal'),
            'duration': a.get('duration'),
            'calories': a.get('calories'),
            'avgHR': a.get('averageHR'),
            'type': a.get('activityType', {}).get('typeKey'),
        })
    return jsonify({'sessions': sessions})

@app.get('/api/strength/<session_id>/exercises')
def get_exercises(session_id):
    try:
        link_manual_exercises_to_activity(session_id)
    except Exception as e:
        print('Strength-passlänkning fel:', e)
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id, exercise, sets, reps, weight, note FROM strength_exercises WHERE session_id=%s AND user_id=%s ORDER BY id',
                        (session_id, uid()))
            rows = cur.fetchall()
    return jsonify({'exercises': [{'id': r[0], 'exercise': r[1], 'sets': r[2], 'reps': r[3], 'weight': r[4], 'note': r[5]} for r in rows]})

def _first_rep_count(reps):
    if reps is None:
        return None
    m = re.search(r'\d+(?:[,.]\d+)?', str(reps))
    if not m:
        return None
    return float(m.group(0).replace(',', '.'))

def _session_day(session_id, activity_dates, created_at):
    sid = str(session_id)
    if sid in activity_dates:
        return activity_dates[sid]
    if re.match(r'^\d{4}-\d{2}-\d{2}$', sid):
        return sid
    try:
        return datetime.fromtimestamp(float(created_at), LOCAL_TZ).date().isoformat()
    except Exception:
        return date.today().isoformat()

@app.get('/api/strength/analysis')
def strength_analysis():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, raw FROM activities WHERE type = ANY(%s) AND user_id=%s",
                        (list(STRENGTH_TYPES), uid()))
            activity_rows = cur.fetchall()
            cur.execute('''
                SELECT id, session_id, exercise, sets, reps, weight, note, created_at
                FROM strength_exercises
                WHERE user_id=%s
                ORDER BY created_at ASC, id ASC
            ''', (uid(),))
            exercise_rows = cur.fetchall()

    activity_dates = {}
    for aid, raw in activity_rows:
        raw = raw or {}
        start = raw.get('startTimeLocal') or raw.get('date')
        if start:
            activity_dates[str(aid)] = str(start)[:10]

    entries = []
    sessions = set()
    weekly_volume = {}
    by_exercise = {}
    now_day = datetime.now(LOCAL_TZ).date()
    cutoff_28 = now_day - timedelta(days=28)

    for ex_id, session_id, exercise, sets, reps, weight, note, created_at in exercise_rows:
        name = (exercise or '').strip()
        if not name:
            continue
        day = _session_day(session_id, activity_dates, created_at)
        try:
            day_obj = datetime.fromisoformat(day[:10]).date()
        except Exception:
            day_obj = now_day
            day = day_obj.isoformat()

        set_count = int(sets or 1)
        rep_count = _first_rep_count(reps)
        kg = float(weight) if weight is not None else None
        volume = round(set_count * rep_count * kg, 1) if rep_count and kg else 0
        e1rm = round(kg * (1 + rep_count / 30), 1) if rep_count and kg else None
        key = name.lower()
        entry = {
            'id': ex_id,
            'sessionId': str(session_id),
            'date': day,
            'exercise': name,
            'sets': set_count,
            'reps': reps,
            'repCount': rep_count,
            'weight': kg,
            'volume': volume,
            'e1rm': e1rm,
            'note': note or '',
        }
        entries.append(entry)
        sessions.add((str(session_id), day))
        monday = (day_obj - timedelta(days=day_obj.weekday())).isoformat()
        weekly_volume[monday] = weekly_volume.get(monday, 0) + volume
        by_exercise.setdefault(key, {'name': name, 'entries': []})['entries'].append(entry)

    exercises = []
    prs = []
    for item in by_exercise.values():
        ex_entries = sorted(item['entries'], key=lambda e: (e['date'], e['id']))
        weighted = [e for e in ex_entries if e['weight']]
        e1rms = [e for e in ex_entries if e['e1rm']]
        latest = ex_entries[-1]
        best = max(e1rms, key=lambda e: e['e1rm']) if e1rms else None
        latest_e1rm = next((e for e in reversed(ex_entries) if e['e1rm']), None)
        previous_e1rm = next((e for e in reversed(ex_entries[:-1]) if e['e1rm']), None)
        delta = round(latest_e1rm['e1rm'] - previous_e1rm['e1rm'], 1) if latest_e1rm and previous_e1rm else None
        total_volume = round(sum(e['volume'] for e in ex_entries), 1)
        trend = 'flat'
        if delta is not None:
            trend = 'up' if delta > 0.2 else 'down' if delta < -0.2 else 'flat'
        if best and latest_e1rm and best['id'] == latest_e1rm['id']:
            prs.append({
                'exercise': item['name'],
                'date': best['date'],
                'e1rm': best['e1rm'],
                'weight': best['weight'],
                'reps': best['reps'],
            })
        exercises.append({
            'exercise': item['name'],
            'sessions': len({e['sessionId'] for e in ex_entries}),
            'sets': sum(e['sets'] for e in ex_entries),
            'totalVolume': total_volume,
            'lastDate': latest['date'],
            'lastWeight': latest['weight'],
            'lastReps': latest['reps'],
            'bestWeight': max((e['weight'] or 0) for e in weighted) if weighted else None,
            'bestE1rm': best['e1rm'] if best else None,
            'currentE1rm': latest_e1rm['e1rm'] if latest_e1rm else None,
            'deltaE1rm': delta,
            'trend': trend,
        })

    exercises.sort(key=lambda e: (e['lastDate'], e['totalVolume']), reverse=True)
    weeks = [{'weekStart': k, 'volume': round(v, 1)} for k, v in sorted(weekly_volume.items())[-8:]]
    recent_sessions = len({s for s in sessions if datetime.fromisoformat(s[1]).date() >= cutoff_28})
    total_volume = round(sum(e['volume'] for e in entries), 1)
    latest_date = max((e['date'] for e in entries), default=None)
    best_lifts = sorted([e for e in exercises if e['bestE1rm']], key=lambda e: e['bestE1rm'], reverse=True)[:5]
    improvements = sorted([e for e in exercises if e['deltaE1rm'] is not None], key=lambda e: e['deltaE1rm'], reverse=True)[:5]

    return jsonify({
        'summary': {
            'exerciseLogs': len(entries),
            'sessions': len(sessions),
            'recentSessions28d': recent_sessions,
            'uniqueExercises': len(exercises),
            'totalVolume': total_volume,
            'latestDate': latest_date,
        },
        'weeks': weeks,
        'exercises': exercises[:30],
        'bestLifts': best_lifts,
        'improvements': improvements,
        'recentPrs': sorted(prs, key=lambda p: p['date'], reverse=True)[:6],
    })

@app.post('/api/strength/<session_id>/exercises')
def add_exercise(session_id):
    data = request.get_json(force=True, silent=True) or {}
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO strength_exercises (session_id,exercise,sets,reps,weight,note,created_at,user_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                        (session_id, data.get('exercise',''), data.get('sets'), data.get('reps',''),
                         data.get('weight'), data.get('note',''), time.time(), uid()))
            new_id = cur.fetchone()[0]
        conn.commit()
    return jsonify({'ok': True, 'id': new_id})

@app.delete('/api/strength/exercises/<int:ex_id>')
def delete_exercise(ex_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM strength_exercises WHERE id=%s AND user_id=%s', (ex_id, uid()))
        conn.commit()
    return jsonify({'ok': True})

# --- Statiska filer ---
# ─────────────────────────────────────────────
# TRÄNINGSPLAN — seed-data (samma som JS-arrayen)
# ─────────────────────────────────────────────
PLAN_SEED = [
    # ── V23 · Återhämtning efter GöteborgsVarvet · ~35 km ─────────────────────
    {'week':23,'dow':1,'type':'run', 'km':6,  'title':'Återhämtningsjogg · 6 km',    'detail':'Z2 · 4:50–5:15/km · Lugn och lätt · Vila musklerna efter halvmaran'},
    {'week':23,'dow':2,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',              'detail':'Z2 · 5:00–5:20/km · Aktiv återhämtning'},
    {'week':23,'dow':3,'type':'lift','km':0,  'title':'Helkropp – intro',             'detail':'Knäböj 3×10, marklyft 3×8, bänkpress 3×10, latsdrag 3×10, plankan 3×45 sek · 60–65% av max'},
    {'week':23,'dow':4,'type':'easy','km':5,  'title':'Lätt Z2 · 5 km',              'detail':'Z2 · 20–25 min · Spola ur benen'},
    {'week':23,'dow':6,'type':'easy','km':10, 'title':'Söndagsjogg · 10 km',         'detail':'Z2 · 5:00–5:20/km · Lugnt och långsamt'},
    # ── V24 · Bas · ~40 km ─────────────────────────────────────────────────────
    {'week':24,'dow':0,'type':'easy','km':6,  'title':'Lätt Z2 · 6 km',              'detail':'Z2 · Aktivering inför veckans kvalitetspass'},
    {'week':24,'dow':1,'type':'run', 'km':9,  'title':'5×1000m intervaller',          'detail':'Uppvärmning 2 km · 5×1000m @ 3:30/km · 2 min joggvila · Nedvarvning 2 km · ~9 km totalt'},
    {'week':24,'dow':2,'type':'lift','km':0,  'title':'Överkropp + core',             'detail':'Bänkpress 4×8, axelpress 3×10, latsdrag 4×8, rodd 3×10, dips 3×max, dead bug 3×12 · 70%'},
    {'week':24,'dow':3,'type':'easy','km':9,  'title':'Medium Z2 · 9 km',            'detail':'Z2 · 5:00–5:15/km · Aerob bas'},
    {'week':24,'dow':4,'type':'lift','km':0,  'title':'Underkropp + core',            'detail':'Knäböj 4×8, RDL 3×10, benpress 3×12, bulgarska utfall 3×8/ben, plankan 4×45 sek · 70–75%'},
    {'week':24,'dow':6,'type':'easy','km':12, 'title':'Långpass · 12 km',             'detail':'Z2 · 5:00–5:20/km · Bygg aerob grund'},
    # ── V25 · Bas · ~45 km ─────────────────────────────────────────────────────
    {'week':25,'dow':0,'type':'easy','km':6,  'title':'Lätt Z2 · 6 km',              'detail':'Z2 · Aktivering'},
    {'week':25,'dow':1,'type':'run', 'km':10, 'title':'Tröskelpass · 10 km',          'detail':'Uppvärm 2 km · 6 km @ 4:05/km (tröskel) · Nedvarv 2 km · Kontrollerat och jämnt'},
    {'week':25,'dow':2,'type':'lift','km':0,  'title':'Helkropp – progressiv',        'detail':'Knäböj 4×8, marklyft 3×6, bänkpress 4×8, axelpress 3×10, latsdrag 4×8, core-circuit 3 ronder · 72%'},
    {'week':25,'dow':3,'type':'easy','km':10, 'title':'Medium Z2 · 10 km',           'detail':'Z2 · 5:00/km · Aerob bas'},
    {'week':25,'dow':5,'type':'run', 'km':8,  'title':'6×600m intervaller',           'detail':'Uppvärm 2 km · 6×600m @ 3:25/km · 90 sek vila · Nedvarvning · Snabbt och kontrollerat'},
    {'week':25,'dow':6,'type':'easy','km':14, 'title':'Långpass · 14 km',             'detail':'Z2 · 5:00–5:15/km · Håll det lugnt, bygg uthållighet'},
    # ── V26 · Basbygge · ~50 km ────────────────────────────────────────────────
    {'week':26,'dow':0,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',              'detail':'Z2 · Aktivering'},
    {'week':26,'dow':1,'type':'run', 'km':11, 'title':'3×2000m tröskel',              'detail':'Uppvärm 2 km · 3×2000m @ 4:00/km · 3 min joggvila · Nedvarv 2 km · ~11 km totalt'},
    {'week':26,'dow':2,'type':'lift','km':0,  'title':'Överkropp tung',               'detail':'Bänkpress 4×6, axelpress 4×6, latsdrag 4×6, smalgreppscurl 3×10, tricepspush 3×10, face pulls 3×15 · 78%'},
    {'week':26,'dow':3,'type':'easy','km':10, 'title':'Medium Z2 · 10 km',           'detail':'Z2 · 5:00/km · Aerob underhåll'},
    {'week':26,'dow':4,'type':'lift','km':0,  'title':'Underkropp tung',              'detail':'Knäböj 4×6, marklyft 4×5, bulgarska utfall 3×8, höftlyft 3×12, vadbågar 4×15, plankan 3×60 sek · 78%'},
    {'week':26,'dow':5,'type':'run', 'km':10, 'title':'Fartlekpass · 10 km',          'detail':'2 km Z2 · 5×(3 min @ 3:50/km + 2 min Z2) · 2 km nedvarvning · Varierat och roligt'},
    {'week':26,'dow':6,'type':'easy','km':15, 'title':'Långpass · 15 km',             'detail':'Z2 · 5:00–5:15/km · Sista 2 km @ 4:30/km'},
    # ── V27 · Basbygge · ~55 km ────────────────────────────────────────────────
    {'week':27,'dow':0,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',              'detail':'Z2'},
    {'week':27,'dow':1,'type':'run', 'km':11, 'title':'Progressionsjogg · 11 km',     'detail':'3 km @ 5:10 · 3 km @ 4:45 · 3 km @ 4:20 · 2 km @ 4:00 · Kontrollerad ansträngning'},
    {'week':27,'dow':2,'type':'lift','km':0,  'title':'Helkropp – styrka',            'detail':'Knäböj 4×6, bänkpress 4×6, marklyft 3×5, axelpress 3×8, latsdrag 4×6, core-circuit · 80%'},
    {'week':27,'dow':3,'type':'easy','km':12, 'title':'Medium Z2 · 12 km',           'detail':'Z2 · 5:00/km'},
    {'week':27,'dow':5,'type':'run', 'km':10, 'title':'4×1200m tempo',                'detail':'Uppvärm 2 km · 4×1200m @ 3:50/km · 2 min vila · Nedvarvning · ~10 km'},
    {'week':27,'dow':6,'type':'easy','km':16, 'title':'Långpass · 16 km',             'detail':'Z2 · 5:00–5:10/km · Lugnt och uthålligt'},
    # ── V28 · Basbygge · ~55 km ────────────────────────────────────────────────
    {'week':28,'dow':0,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',              'detail':'Z2'},
    {'week':28,'dow':1,'type':'run', 'km':12, 'title':'Tröskelpass · 12 km',          'detail':'Uppvärm 2 km · 8 km @ 3:58/km (halvmaratontröskel) · Nedvarv 2 km · Jämnt tempo'},
    {'week':28,'dow':2,'type':'lift','km':0,  'title':'Överkropp + rörlighet',        'detail':'Bänkpress 4×6, axelpress 4×6, latsdrag 4×6, rodd 3×8, dips 3×max, axelrörlighet, t-spine 15 min · 80%'},
    {'week':28,'dow':3,'type':'easy','km':11, 'title':'Medium Z2 · 11 km',           'detail':'Z2 · Aerob bas'},
    {'week':28,'dow':4,'type':'lift','km':0,  'title':'Underkropp + plyometri',       'detail':'Knäböj 4×5, RDL 4×6, benpress 3×10, boxjumps 4×6, höftlyft 3×12, vadhopp 4×15 · 80%'},
    {'week':28,'dow':6,'type':'easy','km':16, 'title':'Långpass · 16 km',             'detail':'Z2 · 5:00/km · Steady state · Sista 3 km lite snabbare'},
    # ── V29 · Basbygge toppar · ~58 km ────────────────────────────────────────
    {'week':29,'dow':0,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2'},
    {'week':29,'dow':1,'type':'run', 'km':11, 'title':'4×2000m @ halvmaraton pace',   'detail':'Uppvärm 2 km · 4×2000m @ 3:52/km · 2:30 min joggvila · Nedvarv 2 km · Race-förnimmelse'},
    {'week':29,'dow':2,'type':'lift','km':0,  'title':'Helkropp – max styrka',        'detail':'Knäböj 5×5, marklyft 4×4, bänkpress 5×5, axelpress 4×5, latsdrag 4×5 · 85%'},
    {'week':29,'dow':3,'type':'easy','km':12, 'title':'Medium Z2 · 12 km',           'detail':'Z2 · 5:00/km'},
    {'week':29,'dow':5,'type':'run', 'km':9,  'title':'10×400m bana',                 'detail':'Uppvärm 2 km · 10×400m @ 3:20/km · 90 sek vila · Nedvarv 2 km · Snabbt och skarpt'},
    {'week':29,'dow':6,'type':'easy','km':18, 'title':'Långpass · 18 km',             'detail':'Z2 · 5:00–5:10/km · Viktigaste passet hittills'},
    # ── V30 · Tröskel/Tempo · ~62 km ──────────────────────────────────────────
    {'week':30,'dow':0,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2'},
    {'week':30,'dow':1,'type':'run', 'km':13, 'title':'Tröskelpass · 13 km',          'detail':'Uppvärm 2 km · 9 km @ 3:55/km · Nedvarv 2 km · Stabilt och kontrollerat'},
    {'week':30,'dow':2,'type':'lift','km':0,  'title':'Överkropp + core',             'detail':'Bänkpress 4×6, axelpress 4×6, latsdrag 4×5, rodd 3×8, plankan 4×60 sek, rygghäv 3×12 · 82%'},
    {'week':30,'dow':3,'type':'easy','km':13, 'title':'Medium Z2 · 13 km',           'detail':'Z2 · Aerob volym'},
    {'week':30,'dow':4,'type':'lift','km':0,  'title':'Underkropp + plyometri',       'detail':'Knäböj 4×5, marklyft 3×4, bulgarska 3×8, boxjumps 4×6, vadbågar 4×15 · 83%'},
    {'week':30,'dow':5,'type':'run', 'km':10, 'title':'6×1000m @ 3:25/km',           'detail':'Uppvärm 2 km · 6×1000m @ 3:25/km · 2 min vila · Nedvarv 2 km · Sharpening'},
    {'week':30,'dow':6,'type':'easy','km':20, 'title':'Långpass · 20 km',             'detail':'Z2 · 5:00/km · Hjärnträning i uthållighet · Håll det lugnt'},
    # ── V31 · Tröskel/Tempo · ~65 km ──────────────────────────────────────────
    {'week':31,'dow':0,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2'},
    {'week':31,'dow':1,'type':'run', 'km':14, 'title':'Halvmaratonpace · 14 km',      'detail':'Uppvärm 2 km · 10 km @ 3:50/km (halvmaran pace) · Nedvarv 2 km · Känn farten'},
    {'week':31,'dow':2,'type':'lift','km':0,  'title':'Helkropp – styrka',            'detail':'Knäböj 4×5, marklyft 4×4, bänkpress 4×5, axelpress 3×6, latsdrag 4×5, core · 83–85%'},
    {'week':31,'dow':3,'type':'easy','km':13, 'title':'Medium Z2 · 13 km',           'detail':'Z2'},
    {'week':31,'dow':5,'type':'run', 'km':12, 'title':'Tröskelpass · 12 km',          'detail':'Uppvärm 2 km · 8 km @ 3:53/km · Nedvarv 2 km · Konsekvent tempo'},
    {'week':31,'dow':6,'type':'easy','km':20, 'title':'Långpass · 20 km',             'detail':'Z2 · 4:58–5:08/km · Starkt och jämnt'},
    # ── V32 · Tröskel/Tempo · ~65 km ──────────────────────────────────────────
    {'week':32,'dow':0,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2'},
    {'week':32,'dow':1,'type':'run', 'km':13, 'title':'5×1600m @ 3:48/km',           'detail':'Uppvärm 2 km · 5×1600m @ 3:48/km · 2:30 min vila · Nedvarv 2 km · Race-specifik'},
    {'week':32,'dow':2,'type':'lift','km':0,  'title':'Överkropp + core',             'detail':'Bänkpress 4×5, axelpress 4×5, latsdrag 4×5, rodd 3×8, core-circuit 3 ronder · 85%'},
    {'week':32,'dow':3,'type':'easy','km':14, 'title':'Medium Z2 · 14 km',           'detail':'Z2'},
    {'week':32,'dow':4,'type':'lift','km':0,  'title':'Underkropp',                   'detail':'Knäböj 4×5, RDL 4×5, bulgarska 3×8, vadhopp 4×15 · 85%'},
    {'week':32,'dow':5,'type':'run', 'km':12, 'title':'Progressionsjogg · 12 km',     'detail':'4 km Z2 · 4 km @ 4:15 · 3 km @ 3:55 · 1 km @ 3:47 · Race-förnimmelse'},
    {'week':32,'dow':6,'type':'easy','km':20, 'title':'Långpass · 20 km',             'detail':'Z2 · Peakpass för långdistans · Sista 4 km @ 4:30/km'},
    # ── V33 · Tröskel · ~60 km ────────────────────────────────────────────────
    {'week':33,'dow':0,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',              'detail':'Z2'},
    {'week':33,'dow':1,'type':'run', 'km':13, 'title':'Halvmaratonpace · 14 km',      'detail':'Uppvärm 2 km · 10 km @ 3:47/km (målpace!) · Nedvarv 2 km · Känn målfarten'},
    {'week':33,'dow':2,'type':'lift','km':0,  'title':'Helkropp – underhåll',         'detail':'Knäböj 3×5, marklyft 3×4, bänkpress 3×5, axelpress 3×6, latsdrag 3×6 · 83% (börja minska volym)'},
    {'week':33,'dow':3,'type':'easy','km':12, 'title':'Medium Z2 · 12 km',           'detail':'Z2'},
    {'week':33,'dow':5,'type':'run', 'km':9,  'title':'8×600m @ 3:25/km',            'detail':'Uppvärm 2 km · 8×600m @ 3:25/km · 90 sek vila · Nedvarv · Sharp och snabb'},
    {'week':33,'dow':6,'type':'easy','km':18, 'title':'Långpass · 18 km',             'detail':'Z2 · 4:58/km · Sista riktiga långpasset'},
    # ── V34 · Tävlingsspecifik · ~68 km ───────────────────────────────────────
    {'week':34,'dow':0,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2'},
    {'week':34,'dow':1,'type':'run', 'km':14, 'title':'Race simulation · 14 km',      'detail':'Uppvärm 2 km · 10 km @ 3:47/km (exakt målpace) · Nedvarv 2 km · Bekräfta formen'},
    {'week':34,'dow':2,'type':'lift','km':0,  'title':'Överkropp – underhåll',        'detail':'Bänkpress 3×5, axelpress 3×5, latsdrag 3×5 · 80% · Håll muskelstimulus utan utmattning'},
    {'week':34,'dow':3,'type':'easy','km':14, 'title':'Medium Z2 · 14 km',           'detail':'Z2'},
    {'week':34,'dow':4,'type':'lift','km':0,  'title':'Underkropp – underhåll',       'detail':'Knäböj 3×5, RDL 3×5, bulgarska 2×8 · 80%'},
    {'week':34,'dow':5,'type':'run', 'km':11, 'title':'Tröskelpass · 11 km',          'detail':'Uppvärm 2 km · 7 km @ 3:50/km · Nedvarv 2 km'},
    {'week':34,'dow':6,'type':'easy','km':22, 'title':'Långpass · 22 km (peak!)',      'detail':'Z2 · 5:00/km · Längsta passet i hela planen · Mentalt starkt'},
    # ── V35 · Tävlingsspecifik · ~70 km ───────────────────────────────────────
    {'week':35,'dow':0,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2'},
    {'week':35,'dow':1,'type':'run', 'km':12, 'title':'3×3000m @ 3:47/km',           'detail':'Uppvärm 2 km · 3×3000m @ 3:47/km · 3 min vila · Nedvarv 2 km · Race-spécifikt'},
    {'week':35,'dow':2,'type':'lift','km':0,  'title':'Helkropp – underhåll',         'detail':'Knäböj 3×4, bänkpress 3×4, marklyft 3×3, axelpress 3×5, latsdrag 3×5 · 80%'},
    {'week':35,'dow':3,'type':'easy','km':14, 'title':'Medium Z2 · 14 km',           'detail':'Z2'},
    {'week':35,'dow':5,'type':'run', 'km':14, 'title':'Tröskelpass · 14 km',          'detail':'Uppvärm 2 km · 10 km @ 3:50/km · Nedvarv 2 km · Stark och kontrollerad'},
    {'week':35,'dow':6,'type':'easy','km':22, 'title':'Långpass · 22 km',             'detail':'Z2 · 5:00/km · Volymens höjdpunkt'},
    # ── V36 · Tävlingsspecifik · ~68 km ───────────────────────────────────────
    {'week':36,'dow':0,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2'},
    {'week':36,'dow':1,'type':'run', 'km':13, 'title':'Race tempo · 13 km',           'detail':'Uppvärm 2 km · 9 km @ 3:47–3:50/km · Nedvarv 2 km · Fokus på ekonomi'},
    {'week':36,'dow':2,'type':'lift','km':0,  'title':'Överkropp lätt',               'detail':'Bänkpress 3×4, axelpress 3×4, latsdrag 3×5 · 78% · Underhåll utan stress'},
    {'week':36,'dow':3,'type':'easy','km':13, 'title':'Medium Z2 · 13 km',           'detail':'Z2'},
    {'week':36,'dow':4,'type':'lift','km':0,  'title':'Underkropp lätt',              'detail':'Knäböj 3×4, RDL 3×5, bulgarska 2×6 · 78%'},
    {'week':36,'dow':5,'type':'run', 'km':10, 'title':'6×1000m @ 3:25/km',           'detail':'Uppvärm 2 km · 6×1000m @ 3:25/km · 2 min vila · Nedvarv · Sharp'},
    {'week':36,'dow':6,'type':'easy','km':20, 'title':'Långpass · 20 km',             'detail':'Z2 · 5:00/km · Sista riktiga långpasset'},
    # ── V37 · Tävlingsspecifik · ~65 km ───────────────────────────────────────
    {'week':37,'dow':0,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',              'detail':'Z2'},
    {'week':37,'dow':1,'type':'run', 'km':14, 'title':'Halvmaratonpace · 14 km',      'detail':'Uppvärm 2 km · 10 km @ 3:47/km · Nedvarv 2 km · Bekräfta formen'},
    {'week':37,'dow':2,'type':'lift','km':0,  'title':'Helkropp – lätt',              'detail':'Knäböj 3×3, bänkpress 3×3, latsdrag 3×5 · 75% · Håll nervmönstret aktivt'},
    {'week':37,'dow':3,'type':'easy','km':12, 'title':'Medium Z2 · 12 km',           'detail':'Z2'},
    {'week':37,'dow':5,'type':'run', 'km':12, 'title':'Progressionsjogg · 12 km',     'detail':'4 km Z2 · 4 km @ 4:10 · 3 km @ 3:52 · 1 km @ 3:40 · Stark avslutning'},
    {'week':37,'dow':6,'type':'easy','km':18, 'title':'Långpass · 18 km',             'detail':'Z2 · 5:00/km · Sista längre volympass'},
    # ── V38 · Avtrappning start · ~55 km ──────────────────────────────────────
    {'week':38,'dow':0,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',              'detail':'Z2'},
    {'week':38,'dow':1,'type':'run', 'km':10, 'title':'4×1000m @ 3:25/km',           'detail':'Uppvärm 2 km · 4×1000m @ 3:25/km · 2 min vila · Nedvarv · Håll spetsen'},
    {'week':38,'dow':2,'type':'lift','km':0,  'title':'Överkropp – lätt',             'detail':'Bänkpress 3×3, axelpress 3×3, latsdrag 3×4 · 73% · Underhåll'},
    {'week':38,'dow':3,'type':'easy','km':10, 'title':'Medium Z2 · 10 km',           'detail':'Z2'},
    {'week':38,'dow':5,'type':'run', 'km':9,  'title':'Tröskelpass · 9 km',           'detail':'Uppvärm 2 km · 5 km @ 3:50/km · Nedvarv 2 km · Skarp och ekonomisk'},
    {'week':38,'dow':6,'type':'easy','km':18, 'title':'Långpass · 18 km',             'detail':'Z2 · 5:00/km · Sista riktigt långa passet'},
    # ── V39 · Taper · ~50 km ──────────────────────────────────────────────────
    {'week':39,'dow':0,'type':'easy','km':6,  'title':'Lätt Z2 · 6 km',              'detail':'Z2'},
    {'week':39,'dow':1,'type':'run', 'km':9,  'title':'Race tempo · 9 km',            'detail':'Uppvärm 2 km · 5 km @ 3:47/km · Nedvarv 2 km · Bekräfta kroppens redo-känsla'},
    {'week':39,'dow':2,'type':'lift','km':0,  'title':'Styrka – underhåll lätt',      'detail':'Knäböj 2×3, bänkpress 2×3, latsdrag 2×4 · 70% · Minimal trötthet'},
    {'week':39,'dow':3,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2'},
    {'week':39,'dow':5,'type':'run', 'km':9,  'title':'4×1000m @ 3:25/km',           'detail':'Uppvärm 2 km · 4×1000m @ 3:25/km · 2 min vila · Nedvarv · Känn spetsen'},
    {'week':39,'dow':6,'type':'easy','km':16, 'title':'Långpass · 16 km',             'detail':'Z2 · 5:00/km · Sista längre pass · Lugnt och tryggt'},
    # ── V40 · Taper djup · ~35 km ─────────────────────────────────────────────
    {'week':40,'dow':0,'type':'easy','km':5,  'title':'Lätt Z2 · 5 km',              'detail':'Z2 · Håll igång benen'},
    {'week':40,'dow':1,'type':'run', 'km':7,  'title':'3×1000m @ 3:25/km + strides', 'detail':'Uppvärm 2 km · 3×1000m @ 3:25/km · 4×100m strides · Känn fräschheten'},
    {'week':40,'dow':3,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',              'detail':'Z2 · 25–30 min · Lugnt'},
    {'week':40,'dow':5,'type':'easy','km':6,  'title':'Lätt jogg + strides',         'detail':'15 min Z2 + 6×80m strides · Håll benen snabba inför loppet'},
    {'week':40,'dow':6,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2 · Mentalt förbered dig · Visualisera loppet'},
    # ── V41 · Tävlingsvecka · ~15 km ─────────────────────────────────────────
    {'week':41,'dow':0,'type':'easy','km':4,  'title':'Lätt aktivering · 4 km',      'detail':'Z2 · 15 min · 4×80m strides i slutet · Håll igång'},
    {'week':41,'dow':1,'type':'run', 'km':4,  'title':'Kort shakeout',               'detail':'10 min Z2 + 3×100m strides @ tävlingsfart · Kort och piggt'},
    {'week':41,'dow':2,'type':'rest','km':0,  'title':'Vila',                        'detail':'Fullständig vila · Ät kolhydratrikt · Sov länge · Packa väskan'},
    {'week':41,'dow':3,'type':'rest','km':0,  'title':'Vila / rörlighet',             'detail':'Lätt stretching 20 min · Inga hårda övningar · Mental förberedelse'},
    {'week':41,'dow':4,'type':'rest','km':0,  'title':'Vila · redo!',                'detail':'Fullständig vila · Ät bra · Lägg upp trasén · Sov tidigt'},
    {'week':41,'dow':5,'type':'race','km':21, 'title':'TÄVLING — Halvmaraton sub 1:20','detail':'MÅL: 1:19:59 · Pace: 3:47/km · Km 1–5: 3:50/km (varm upp) · Km 6–18: 3:47/km · Km 19–21: ge allt · Lycka till!'},
]

def seed_plan():
    """Fyll plan_sessions från PLAN_SEED om tabellen är tom (för user_id=1)."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM plan_sessions WHERE user_id=1')
            if cur.fetchone()[0] > 0:
                return  # redan seedat
            for s in PLAN_SEED:
                cur.execute('''INSERT INTO plan_sessions
                    (week, dow, type, km, title, detail, status, original_week, original_dow, user_id)
                    VALUES (%s,%s,%s,%s,%s,%s,'planned',%s,%s,%s)''',
                    (s['week'], s['dow'], s['type'], s['km'],
                     s['title'], s['detail'], s['week'], s['dow'], 1))
        conn.commit()
    print(f'Plan seedat: {len(PLAN_SEED)} pass')

def reseed_plan():
    """Ersätt alla planerade pass med ny PLAN_SEED. Behåller completed/missed/skipped som historik."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM plan_sessions WHERE status = 'planned' AND user_id=1")
            for s in PLAN_SEED:
                cur.execute('''INSERT INTO plan_sessions
                    (week, dow, type, km, title, detail, status, original_week, original_dow, user_id)
                    VALUES (%s,%s,%s,%s,%s,%s,'planned',%s,%s,%s)''',
                    (s['week'], s['dow'], s['type'], s['km'],
                     s['title'], s['detail'], s['week'], s['dow'], 1))
        conn.commit()
    print(f'Plan omseedad: {len(PLAN_SEED)} nya pass')

try:
    seed_plan()
except Exception as e:
    print('Seed-fel:', e)


# ─────────────────────────────────────────────
# PLAN API
# ─────────────────────────────────────────────
@app.get('/api/plan')
def get_plan():
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('SELECT * FROM plan_sessions WHERE user_id=%s ORDER BY week, dow', (uid(),))
            rows = cur.fetchall()
    return jsonify({'sessions': [dict(r) for r in rows]})

@app.patch('/api/plan/<int:session_id>')
def update_session(session_id):
    data = request.json or {}
    allowed = {'status','week','dow','title','detail','km','ai_note'}
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({'error': 'No valid fields'}), 400
    fields['modified_at'] = time.time()
    set_clause = ', '.join(f'{k} = %s' for k in fields)
    vals = list(fields.values()) + [session_id, uid()]
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(f'UPDATE plan_sessions SET {set_clause} WHERE id = %s AND user_id = %s', vals)
        conn.commit()
    return jsonify({'ok': True})


# ─────────────────────────────────────────────
# AKTIVITETSMATCHNING
# ─────────────────────────────────────────────
def _iso_week_dow(d):
    """Returnera (iso_week, dow_0mon) för ett date-objekt."""
    iso = d.isocalendar()
    return iso[1], iso[2] - 1  # dow: 0=mån

def match_activities_to_plan(days_back=7, user_id=1):
    """
    Jämför Garmin-aktiviteter mot planerade pass de senaste N dagarna.
    Markerar pass som completed eller missed. Re-utvärderar även 'missed'
    (om en aktivitet synkats i efterhand) men rör aldrig skipped/rescheduled.
    Idag hoppas över (dagen är inte slut). Körs efter varje synk + 07:30.
    """
    today = date.today()
    run_types  = {'running','track_running','treadmill_running','trail_running'}
    lift_types = {'strength_training','fitness_equipment'}

    with db() as conn:
        for i in range(0, days_back + 1):
            day = today - timedelta(days=i)
            wk, dw = _iso_week_dow(day)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute('''SELECT * FROM plan_sessions
                    WHERE week = %s AND dow = %s AND status IN ('planned','missed','skipped') AND user_id = %s''',
                    (wk, dw, user_id))
                planned = cur.fetchall()
                if not planned:
                    continue
                cur.execute('''SELECT raw FROM activities
                    WHERE date >= %s AND date < %s AND user_id = %s''',
                    (day.isoformat(), (day + timedelta(days=1)).isoformat(), user_id))
                acts = [r['raw'] for r in cur.fetchall()]

            did_run  = any(a.get('activityType',{}).get('typeKey','') in run_types for a in acts)
            did_lift = any(a.get('activityType',{}).get('typeKey','') in lift_types for a in acts)

            with conn.cursor() as cur:
                for p in planned:
                    if p['type'] in ('run','easy','race'):
                        completed = did_run
                    elif p['type'] == 'lift':
                        completed = did_lift
                    elif p['type'] == 'rest':
                        completed = True  # vilodag räknas alltid som genomförd
                    else:
                        completed = False
                    if completed:
                        new_status = 'completed'
                    elif i == 0:
                        continue
                    elif p['status'] == 'skipped':
                        continue
                    else:
                        new_status = 'missed'
                    if new_status != p['status']:
                        cur.execute('''UPDATE plan_sessions SET status = %s, modified_at = %s
                            WHERE id = %s AND user_id = %s''', (new_status, time.time(), p['id'], user_id))
        conn.commit()
    print(f'Activity matching complete (last {days_back} days)')


def _parse_garmin_epoch(value, assume_utc=False):
    """Return epoch seconds for Garmin timestamps in numeric or string form."""
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        # Garmin payloads can use either seconds or milliseconds.
        return float(value) / 1000.0 if value > 100000000000 else float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.replace('.', '', 1).isdigit():
            return _parse_garmin_epoch(float(text), assume_utc=assume_utc)
        try:
            normalized = text.replace('Z', '+00:00')
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None and assume_utc:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def _activity_local_date(raw):
    for key in ('startTimeLocal', 'startTimeGMT', 'calendarDate'):
        val = raw.get(key)
        if val:
            return str(val)[:10]
    return None


def _activity_start_epoch(raw):
    return (
        _parse_garmin_epoch(raw.get('startTimeLocal')) or
        _parse_garmin_epoch(raw.get('beginTimestamp'), assume_utc=True) or
        _parse_garmin_epoch(raw.get('startTimeGMT'), assume_utc=True)
    )


def link_manual_exercises_to_activity(session_id):
    """Attach date-keyed exercises to one concrete Garmin strength activity."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT raw FROM activities WHERE id=%s AND type = ANY(%s)", (session_id, list(STRENGTH_TYPES)))
            row = cur.fetchone()
            if not row:
                return 0
            local = _activity_local_date(row[0])
            if not local:
                return 0
            cur.execute("UPDATE strength_exercises SET session_id=%s WHERE session_id=%s", (str(session_id), local))
            linked = cur.rowcount
        conn.commit()
    if linked:
        print(f'Strength: länkade {linked} manuella övningar till Garmin-pass {session_id}')
    return linked


def link_manual_exercises_to_activities():
    """Koppla manuellt loggade övningar (sparade under datum-nyckel 'YYYY-MM-DD' i
    Today's workout) till Garmin-styrkepasset som laddats upp samma dag, så de hamnar
    på rätt aktivitet i historiken. Vid flera pass samma dag väljs det som ligger
    närmast övningarnas loggtid. Idempotent — när raderna fått aktivitets-id rörs de ej."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(r"""
                SELECT session_id, avg(created_at)
                FROM strength_exercises
                WHERE session_id ~ '^\d{4}-\d{2}-\d{2}$'
                GROUP BY session_id
            """)
            date_rows = cur.fetchall()
            date_keys = [r[0] for r in date_rows]
            if not date_keys:
                return
            cur.execute("SELECT id, raw FROM activities WHERE type = ANY(%s)", (list(STRENGTH_TYPES),))
            strength = cur.fetchall()
    if not strength:
        return
    by_date = {}
    for aid, raw in strength:
        local = _activity_local_date(raw)
        if not local:
            continue
        by_date.setdefault(local, []).append((str(aid), _activity_start_epoch(raw)))

    linked = 0
    with db() as conn:
        with conn.cursor() as cur:
            for dk, avg_created in date_rows:
                cands = by_date.get(dk)
                if not cands:
                    continue  # inget Garmin-pass den dagen än → vänta
                if any(c[1] is not None for c in cands) and avg_created is not None:
                    best = min(cands, key=lambda c: abs((c[1] or 0) - float(avg_created)) if c[1] else float('inf'))
                else:
                    best = cands[0]
                cur.execute("UPDATE strength_exercises SET session_id=%s WHERE session_id=%s", (best[0], dk))
                linked += cur.rowcount
        conn.commit()
    if linked:
        print(f'Strength: länkade {linked} manuella övningar till Garmin-pass')


def run_sync(count=50, username=None, user_id=1):
    """Hämta senaste aktiviteter, spara, rensa cache och matcha mot planen.
    Används av både /api/sync och den återkommande autosynken."""
    if username is None:
        username = list(USERS.keys())[0] if USERS else 'hugo'
    client = get_garmin(username)
    acts = client.get_activities(0, count)
    save_activities(acts, user_id)
    try:
        link_manual_exercises_to_activities()
    except Exception as e:
        print('Strength-länkning fel:', e)
    clear_cache('health', 'analysis', 'training_review', user_id=user_id)
    try:
        match_activities_to_plan(user_id=user_id)
    except Exception as e:
        print('Matchning efter synk fel:', e)
    try:
        maybe_run_daily_routine()
    except Exception as e:
        print('Daglig rutin fel:', e)
    return len(acts)


# ─────────────────────────────────────────────
# AI-JUSTERARE
# ─────────────────────────────────────────────
def ai_adjust_plan(user_request=None):
    """
    Kärnan i den automatiska planjusteringen.
    Körs kl 07:30 varje morgon efter sömndata kommit in.
    user_request: valfri fritext från användaren (t.ex. "jag vill gymma idag
    istället för att springa") som prioriteras högt i coachens beslut.
    """
    if not ANTHROPIC_KEY:
        print('AI adjustment: API key missing')
        return

    today     = date.today()
    iso_week  = today.isocalendar()[1]

    first_user = list(USERS.keys())[0] if USERS else 'hugo'
    first_uid  = USERS.get(first_user, {}).get('id', 1)

    # 1. Synka Garmin och hälsodata
    try:
        client = get_garmin(first_user)
        acts = client.get_activities(0, 20)
        save_activities(acts, first_uid)
        # Rensa hälso-cache så färsk sömndata hämtas
        clear_cache('health', 'training_load', user_id=first_uid)
    except Exception as e:
        print('AI adjustment: Garmin error', e)

    # 2. Hämta hälsodata
    try:
        client = get_garmin(first_user)
        today_str = today.isoformat()
        sleep     = client.get_sleep_data(today_str)
        readiness = client.get_training_readiness(today_str)
        hrv       = client.get_hrv_data(today_str)
        tl_status = client.get_training_status(today_str)

        s         = sleep.get('dailySleepDTO', {})
        sleep_score = (s.get('sleepScores') or {}).get('overall', {}).get('value')
        deep_pct  = round(s.get('deepSleepSeconds',0) / s.get('sleepTimeSeconds',1) * 100) if s.get('sleepTimeSeconds') else 0
        rem_pct   = round(s.get('remSleepSeconds',0)  / s.get('sleepTimeSeconds',1) * 100) if s.get('sleepTimeSeconds') else 0
        total_h   = round(s.get('sleepTimeSeconds',0) / 3600, 1)
        ready_score = (readiness[0] if readiness else {}).get('score')
        hrv_sum   = hrv.get('hrvSummary', {})
        hrv_avg   = hrv_sum.get('lastNightAvg')
        hrv_weekly = hrv_sum.get('weeklyAvg')
        hrv_pct   = round((hrv_avg / hrv_weekly) * 100) if hrv_weekly and hrv_avg else None

        dev_map   = tl_status.get('mostRecentTrainingStatus',{}).get('latestTrainingStatusData',{})
        dev       = next(iter(dev_map.values()), {})
        acwr_dto  = dev.get('acuteTrainingLoadDTO', {})
        acute     = acwr_dto.get('dailyTrainingLoadAcute')
        chronic   = acwr_dto.get('dailyTrainingLoadChronic')
        acwr      = acwr_dto.get('dailyAcuteChronicWorkloadRatio')
    except Exception as e:
        print('AI adjustment: health data error', e)
        sleep_score = deep_pct = rem_pct = total_h = None
        ready_score = hrv_avg = hrv_weekly = hrv_pct = None
        acute = chronic = acwr = None

    # 3. Hämta missade pass + kommande 14 dagar
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('''SELECT * FROM plan_sessions
                WHERE status = 'missed' AND week >= %s AND user_id = %s
                ORDER BY week, dow''', (iso_week - 1, first_uid))
            missed = [dict(r) for r in cur.fetchall()]

            cur.execute('''SELECT * FROM plan_sessions
                WHERE status = 'planned' AND week >= %s AND user_id = %s
                ORDER BY week, dow LIMIT 20''', (iso_week, first_uid))
            upcoming = [dict(r) for r in cur.fetchall()]

            # Genomförd km och load denna vecka
            cur.execute('''SELECT raw FROM activities WHERE date >= %s AND user_id = %s''',
                ((today - timedelta(days=today.weekday())).isoformat(), first_uid))
            week_acts = [r['raw'] for r in cur.fetchall()]

    completed_km   = sum((a.get('distance',0) or 0)/1000 for a in week_acts
                         if any(t in (a.get('activityType',{}).get('typeKey',''))
                                for t in ('running','track_running','treadmill_running','trail_running')))
    completed_load = sum(a.get('activityTrainingLoad',0) or 0 for a in week_acts)

    weekly_km_plan = {23:35,24:40,25:45,26:50,27:55,28:55,29:58,30:62,31:65,32:65,33:60,34:68,35:70,36:68,37:65,38:55,39:50,40:35,41:15}
    planned_km = weekly_km_plan.get(iso_week, 40)
    week_cap   = round(planned_km * 1.1)

    # 4. Google Calendar — hämta från cache
    cal_row = get_cache('gcal_events', first_uid)
    gcal_str = ''
    if cal_row:
        upcoming_evs = []
        for ev in (cal_row[0] or []):
            try:
                ev_date = datetime.fromisoformat(ev.get('start','')[:10]).date()
                if today <= ev_date <= today + timedelta(days=14):
                    desc_part = f" — {ev['desc']}" if ev.get('desc') else ''
                    upcoming_evs.append(f"- {ev_date}: {ev.get('title','')}{desc_part}")
            except Exception:
                continue
        gcal_str = '\n'.join(upcoming_evs)

    # 5. Bygg AI-prompt
    def _sess(s):
        return {'id': s['id'], 'week': s['week'], 'day': s['dow'], 'type': s['type'],
                'km': s['km'], 'title': s['title'], 'detail': s['detail']}
    missed_json   = json.dumps([_sess(s) for s in missed],   ensure_ascii=False, indent=2) if missed else '(no missed sessions)'
    upcoming_json = json.dumps([_sess(s) for s in upcoming], ensure_ascii=False, indent=2)

    request_block = ''
    if user_request:
        request_block = f"""

=== RUNNER'S EXPLICIT REQUEST FOR TODAY (HIGH PRIORITY) ===
The runner has personally asked for this change. Honor it as far as it is sensible and safe, and adjust the surrounding plan so the training logic stays intact (e.g. if they want strength instead of a run today, move today's run to a suitable nearby day or fold it into another run, and place/keep a strength session today). Only push back if the request would clearly harm recovery or the goal — and then explain why in coaching_notes.
Request: "{user_request.strip()}"
"""

    prompt = f"""You are an experienced running coach with deep knowledge of physiology and training planning. You are working with a runner whose goal is a half marathon under 1:20 (3:47/km) on October 10, 2026. Current best: 1:26:19. Secondary goal: build a strong body in all areas - running strength, upper body, core, mobility. The plan runs W23-41 with phases: recovery -> base building -> threshold/tempo -> race-specific -> taper. Always respond in Swedish (svenska). All JSON text fields must be written in Swedish.

TODAY: {today} (week {iso_week}, day {today.weekday()}, where 0=Monday)
{request_block}
=== RUNNER STATUS ===

Sleep today:
- Score: {sleep_score or 'missing'}/100
- Total: {total_h or 'missing'} h · Deep sleep: {deep_pct or 'missing'}% · REM: {rem_pct or 'missing'}%

Recovery:
- Garmin readiness: {ready_score or 'missing'}/100
- Night HRV: {hrv_avg or 'missing'} ms · Weekly average: {hrv_weekly or 'missing'} ms · Difference: {(str(hrv_pct - 100) + '%') if hrv_pct else 'missing'}

Training load (ACWR):
- Acute: {acute or 'missing'} · Chronic: {chronic or 'missing'} · Ratio: {acwr or 'missing'}
- Reference: <0.8 undertrained, 0.8-1.3 optimal, >1.3 injury risk

Week status W{iso_week}:
- Completed running: {completed_km:.1f} km · Planned weekly cap: {week_cap} km
- Completed total load: {round(completed_load)}

=== SESSIONS THAT NEED A DECISION ===

Missed sessions:
{missed_json}

Upcoming planned sessions, next 14 days:
{upcoming_json}

Google Calendar, next 14 days, affecting recovery and timing:
{gcal_str or '(no events)'}

=== YOUR TASK ===

Analyze the situation as a coach and make the best decisions for the runner's long-term development. You may:

- Add a new session: use this for explicit requests to add training on an empty day or to create an extra optional session
- Reschedule sessions: provide the new week and day
- Skip sessions: when they do not add value given fatigue or context
- Modify session content: change distance, pace, type, or structure
- Combine logic: for example reschedule and modify the same session
- Keep sessions unchanged: when that is the right decision

Think like a coach, not a rule sheet. Reason about examples like:
- If three hard sessions are stacked in a row, redistribute them to avoid accumulated fatigue
- If one session was missed but the next one fits the structure well, it may be better to make the next session slightly longer than to cram in the missed one
- If the runner is in good shape, with high HRV and good sleep, use that readiness carefully
- If the runner is tired, protect quality adaptations: one good session is better than three mediocre ones
- Consider Google Calendar: a stressful workday affects recovery
- Avoid stacking more than two hard sessions in a row, including run quality or high-load strength work
- Keep sessions with status completed or skipped unchanged

Write a concise explanation in coaching_notes before the decisions.

Return ONLY this JSON, with no comments outside it:
{{
  "coaching_notes": "<2-4 Swedish sentences explaining how you interpret the situation and why you chose this approach>",
  "changes": [
    {{
      "session_id": <int or null for add>,
      "action": "add|reschedule|skip|keep|modify",
      "new_week": <int or null>,
      "new_dow": <int 0-6 or null>,
      "type": "run|easy|race|lift|rest|null",
      "new_km": <float or null>,
      "new_title": "<Swedish string or null>",
      "new_detail": "<concise workout instructions only, max 140 characters; put reasoning in coaching_notes/reason, or null if unchanged>",
      "reason": "<one Swedish sentence explaining this decision>"
    }}
  ],
  "summary": "<one Swedish sentence summarizing today's adjustments>"
}}"""

    # 6. Anropa Claude
    try:
        resp = requests.post('https://api.anthropic.com/v1/messages',
            json={'model': 'claude-sonnet-4-6', 'max_tokens': 3000,
                  'messages': [{'role': 'user', 'content': prompt}]},
            headers={'x-api-key': ANTHROPIC_KEY, 'anthropic-version': '2023-06-01',
                     'content-type': 'application/json'})
        text = resp.json()['content'][0]['text'].strip().replace('```json','').replace('```','').strip()
        result = json.loads(text)
    except Exception as e:
        print('AI adjustment: Claude error', e)
        return

    # 7. Applicera ändringarna på DB
    changes_applied = 0
    with db() as conn:
        with conn.cursor() as cur:
            for change in result.get('changes', []):
                sid    = change.get('session_id')
                action = change.get('action')
                if action == 'keep':
                    continue
                if action == 'add':
                    new_week = change.get('new_week')
                    new_dow  = change.get('new_dow')
                    title    = change.get('new_title')
                    detail   = change.get('new_detail')
                    typ      = change.get('type') or 'easy'
                    km       = change.get('new_km') if change.get('new_km') is not None else 0
                    if new_week and new_dow is not None and title and detail:
                        cur.execute('''INSERT INTO plan_sessions
                            (week, dow, type, km, title, detail, status, original_week, original_dow, ai_note, modified_at, user_id)
                            VALUES (%s,%s,%s,%s,%s,%s,'planned',%s,%s,%s,%s,%s)''',
                            (new_week, new_dow, typ, km, title, detail, new_week, new_dow,
                             change.get('reason',''), time.time(), first_uid))
                        changes_applied += 1
                    continue
                if not sid:
                    continue
                if action == 'skip':
                    cur.execute('''UPDATE plan_sessions
                        SET status='skipped', ai_note=%s, modified_at=%s WHERE id=%s AND user_id=%s''',
                        (change.get('reason',''), time.time(), sid, first_uid))
                    changes_applied += 1
                elif action == 'reschedule':
                    new_week = change.get('new_week')
                    new_dow  = change.get('new_dow')
                    if new_week and new_dow is not None:
                        # Tillåt även innehållsuppdatering vid ombokning
                        extra_sets = []
                        extra_vals = []
                        if change.get('new_km') is not None:
                            extra_sets.append('km=%s'); extra_vals.append(change['new_km'])
                        if change.get('new_title'):
                            extra_sets.append('title=%s'); extra_vals.append(change['new_title'])
                        if change.get('new_detail'):
                            extra_sets.append('detail=%s'); extra_vals.append(change['new_detail'])
                        extra_sql = (',' + ','.join(extra_sets)) if extra_sets else ''
                        cur.execute(f'''UPDATE plan_sessions
                            SET status='planned', week=%s, dow=%s,
                                ai_note=%s, modified_at=%s{extra_sql} WHERE id=%s AND user_id=%s''',
                            [new_week, new_dow, change.get('reason',''), time.time()] + extra_vals + [sid, first_uid])
                        changes_applied += 1
                elif action == 'modify':
                    # Ändra passinnehåll utan att flytta det
                    mod_sets = ['ai_note=%s', 'modified_at=%s']
                    mod_vals = [change.get('reason',''), time.time()]
                    if change.get('new_km') is not None:
                        mod_sets.append('km=%s'); mod_vals.append(change['new_km'])
                    if change.get('new_title'):
                        mod_sets.append('title=%s'); mod_vals.append(change['new_title'])
                    if change.get('new_detail'):
                        mod_sets.append('detail=%s'); mod_vals.append(change['new_detail'])
                    mod_vals.extend([sid, first_uid])
                    cur.execute(f'''UPDATE plan_sessions
                        SET {','.join(mod_sets)} WHERE id=%s AND status='planned' AND user_id=%s''',
                        mod_vals)
                    changes_applied += 1
        conn.commit()

    summary        = result.get('summary', '')
    coaching_notes = result.get('coaching_notes', '')
    print(f'AI adjustment complete: {changes_applied} changes. {summary}')
    if coaching_notes:
        print(f'Coach: {coaching_notes}')
    set_cache('last_plan_adjustment', {
        'date': today.isoformat(),
        'changes': changes_applied,
        'summary': summary,
        'coaching_notes': coaching_notes,
        'user_request': user_request or None
    }, first_uid)


# ─────────────────────────────────────────────
# MANUELL TRIGGER (för testning)
# ─────────────────────────────────────────────
@app.post('/api/plan/reseed')
def api_reseed():
    """Ersätt alla planerade pass med ny PLAN_SEED (behåller historik)."""
    try:
        reseed_plan()
        return jsonify({'ok': True, 'sessions': len(PLAN_SEED)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def manual_adjust_disabled():
    """Trigga AI-justeringen manuellt (t.ex. för testning)."""
    return jsonify({'error': 'Automatic plan coach is disabled'}), 410

@app.post('/api/plan/request')
def plan_request():
    """Fritext-önskemål från användaren → AI:n bygger om schemat efter det."""
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'Skriv vad du vill ändra först.'}), 400
    if len(text) > 500:
        return jsonify({'error': 'Keep the request under 500 characters.'}), 400
    if not ANTHROPIC_KEY or ANTHROPIC_KEY.startswith('sk-ant-placeholder'):
        return jsonify({'error': 'AI key required'}), 503
    try:
        match_activities_to_plan(user_id=uid())
        ai_adjust_plan(user_request=text)
        first_uid = USERS.get(list(USERS.keys())[0] if USERS else 'hugo', {}).get('id', 1)
        row = get_cache('last_plan_adjustment', first_uid)
        return jsonify({'ok': True, 'result': row[0] if row else {}})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.get('/api/plan/status')
def plan_status():
    """Senaste AI-justeringens status."""
    return jsonify({'date': None, 'changes': 0, 'summary': '', 'coaching_notes': ''})


# ─────────────────────────────────────────────
# SCHEDULER — kör kl 07:30 varje morgon
# ─────────────────────────────────────────────
def maybe_run_daily_routine():
    """Den dagliga rutinen körs EN gång per dag — men först när dagens hälsodata
    faktiskt har synkat. Ingen gissad klockslag, inget 'recovery unavailable'.
    Drivs av autosynken (var 3:e timme) + varje manuell synk. Kör bara för user_id=1."""
    first_user = list(USERS.keys())[0] if USERS else 'hugo'
    first_uid  = USERS.get(first_user, {}).get('id', 1)
    row = get_cache('last_daily_history', first_uid)
    if row and row[0].get('date') == date.today().isoformat():
        return  # redan kört idag
    today = date.today().isoformat()
    try:
        client = get_garmin(first_user)
        readiness = client.get_training_readiness(today)
        sleep = client.get_sleep_data(today)
    except Exception as e:
        print('Daglig rutin: kunde inte kolla hälsodata', e)
        return
    sleep_ok = bool((sleep.get('dailySleepDTO', {}) or {}).get('sleepTimeSeconds'))
    ready_ok = bool(readiness and (readiness[0] or {}).get('score'))
    if not (sleep_ok or ready_ok):
        print('Daglig rutin: dagens hälsodata inte synkad än — väntar till nästa synk')
        return
    print('Daglig rutin: dagens data finns → matchning + historik')
    collect_health_history(username=first_user)
    collect_metric_history(username=first_user)
    clear_cache('insights', user_id=first_uid)
    set_cache('last_daily_history', {'date': today}, first_uid)

def auto_sync_job():
    try:
        n = run_sync()
        print(f'[{datetime.now().strftime("%H:%M")}] Auto-sync klar: {n} aktiviteter')
    except Exception as e:
        print('Auto-sync fel:', e)

scheduler = BackgroundScheduler(timezone='Europe/Stockholm')
scheduler.add_job(auto_sync_job, 'interval', hours=3)
scheduler.start()
print('Scheduler active: data-driven daglig rutin via autosynk var 3:e timme')

# Bootstrappa hälsohistorik + fitness-mätare i bakgrunden (blockerar inte serverstarten)
def _bootstrap_history():
    first_user = list(USERS.keys())[0] if USERS else 'hugo'
    collect_health_history(14, username=first_user)
    collect_metric_history(45, username=first_user)
threading.Thread(target=_bootstrap_history, daemon=True).start()


@app.get('/')
def index():
    return send_from_directory('public', 'index.html')

@app.get('/<path:path>')
def static_files(path):
    return send_from_directory('public', path)

if __name__ == '__main__':
    print('Dashboard startar på http://localhost:3000')
    print('Tryck Ctrl+C för att stänga')
    app.run(host='0.0.0.0', port=3000, debug=False)
