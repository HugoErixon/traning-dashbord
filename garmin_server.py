from flask import Flask, request, jsonify, send_from_directory
from garminconnect import Garmin
from pathlib import Path
from dotenv import dotenv_values
import json, time, requests, psycopg2, psycopg2.extras
from datetime import date, datetime, timedelta
import os
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
PASSWORD    = config.get('SITE_PASSWORD', 'hugo123')
ANTHROPIC_KEY = config.get('ANTHROPIC_API_KEY', '')
TOKEN_DIR     = str(Path.home() / '.garminconnect')
DATABASE_URL  = config.get('DATABASE_URL', '')
GCAL_ID       = config.get('GOOGLE_CALENDAR_ID', 'primary')
GCAL_CREDS    = 'google_credentials.json'
GCAL_TOKEN    = 'google_token.json'
GCAL_SCOPES   = ['https://www.googleapis.com/auth/calendar.readonly']

# --- Databas ---
def db():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
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
        conn.commit()
    print('Databas: klar')

try:
    setup_db()
except Exception as e:
    print('Databas fel:', e)

# --- Garmin ---
_garmin = None

def get_garmin():
    global _garmin
    if _garmin:
        return _garmin
    g = Garmin()
    g.login(tokenstore=TOKEN_DIR)
    _garmin = g
    return g

def save_activities(activities):
    with db() as conn:
        with conn.cursor() as cur:
            for a in activities:
                try:
                    cur.execute('''INSERT INTO activities (id,name,date,type,distance,duration,avg_hr,raw,created_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (id) DO UPDATE SET raw=EXCLUDED.raw, name=EXCLUDED.name''',
                        (a.get('activityId'), a.get('activityName'), a.get('startTimeLocal'),
                         a.get('activityType', {}).get('typeKey'),
                         a.get('distance'), a.get('duration'), a.get('averageHR'),
                         json.dumps(a), time.time()))
                except Exception as e:
                    print('Spara aktivitet fel:', e)
        conn.commit()

def get_cache(key):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value, updated_at FROM cache WHERE key=%s", (key,))
            return cur.fetchone()

def set_cache(key, value):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO cache (key, value, updated_at) VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at''',
                (key, json.dumps(value), time.time()))
        conn.commit()

def clear_cache(*keys):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM cache WHERE key = ANY(%s)", (list(keys),))
        conn.commit()

# --- Auth ---
@app.before_request
def check_auth():
    if not request.path.startswith('/api/'):
        return
    if request.path == '/api/login':
        return
    if request.host.startswith('localhost') or request.host.startswith('127.0.0.1'):
        return
    if request.headers.get('x-site-password') != PASSWORD:
        return jsonify({'error': 'Unauthorized'}), 401

# --- Endpoints ---
@app.post('/api/login')
def login():
    if (request.json or {}).get('password') == PASSWORD:
        return jsonify({'ok': True})
    return jsonify({'ok': False}), 401

@app.get('/api/status')
def status():
    return jsonify({'status': 'ok'})

@app.get('/api/activities')
def activities():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT raw FROM activities ORDER BY date DESC LIMIT 50')
            rows = cur.fetchall()
    if rows:
        return jsonify({'activities': [r[0] for r in rows], 'source': 'database'})
    try:
        client = get_garmin()
        acts = client.get_activities(0, 50)
        save_activities(acts)
        return jsonify({'activities': acts, 'source': 'garmin'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.get('/api/health')
def health_data():
    today = date.today().isoformat()
    row = get_cache('health')
    if row and (time.time() - row[1]) < 30 * 60:
        return jsonify(row[0])

    try:
        client = get_garmin()
        sleep     = client.get_sleep_data(today)
        hrv       = client.get_hrv_data(today)
        bb        = client.get_body_battery(today, today)
        stress    = client.get_stress_data(today)
        readiness = client.get_training_readiness(today)
        hr        = client.get_heart_rates(today)
        resp      = client.get_respiration_data(today)
        spo2      = client.get_spo2_data(today)

        s = sleep.get('dailySleepDTO', {})
        total_sleep_sec = s.get('sleepTimeSeconds', 0)
        deep_sec  = s.get('deepSleepSeconds', 0)
        rem_sec   = s.get('remSleepSeconds', 0)
        sleep_scores = s.get('sleepScores') or {}
        sleep_score_val = sleep_scores.get('overall', {}).get('value') if isinstance(sleep_scores, dict) else None

        hrv_sum = hrv.get('hrvSummary', {})
        hrv_pct = round((hrv_sum.get('lastNightAvg', 0) / hrv_sum.get('weeklyAvg', 1)) * 100) if hrv_sum.get('weeklyAvg') else None

        bb_today = bb[0] if bb else {}
        bb_vals  = bb_today.get('bodyBatteryValuesArray', [])
        bb_now   = bb_vals[-1][1] if bb_vals else None
        bb_max   = max(v[1] for v in bb_vals) if bb_vals else None

        ready    = readiness[0] if readiness else {}
        avg_resp = resp.get('avgWakingRespirationValue') or resp.get('avgRespirationValue')
        sleep_resp = resp.get('avgSleepRespirationValue')
        avg_spo2 = spo2.get('averageSpO2')
        if avg_spo2: avg_spo2 = round(avg_spo2)

        result = {
            'date': today,
            'readiness':   {'score': ready.get('score'), 'level': ready.get('level'), 'feedback': ready.get('feedbackShort')},
            'hrv':         {'lastNightAvg': hrv_sum.get('lastNightAvg'), 'weeklyAvg': hrv_sum.get('weeklyAvg'), 'status': hrv_sum.get('status'), 'pct': hrv_pct},
            'restingHR':   {'value': hr.get('restingHeartRate'), 'sevenDayAvg': hr.get('lastSevenDaysAvgRestingHeartRate'), 'min': hr.get('minHeartRate')},
            'sleep':       {'totalSec': total_sleep_sec, 'deepSec': deep_sec, 'remSec': rem_sec, 'score': sleep_score_val,
                            'deepPct': round(deep_sec/total_sleep_sec*100) if total_sleep_sec else 0,
                            'remPct':  round(rem_sec/total_sleep_sec*100)  if total_sleep_sec else 0},
            'bodyBattery': {'current': bb_now, 'max': bb_max, 'charged': bb_today.get('charged'), 'drained': bb_today.get('drained')},
            'stress':      {'avg': stress.get('avgStressLevel'), 'max': stress.get('maxStressLevel')},
            'respiration': {'avg': round(avg_resp) if avg_resp else None, 'sleepAvg': round(sleep_resp) if sleep_resp else None},
            'spo2':        {'avg': avg_spo2, 'min': spo2.get('lowestSpO2')},
        }
        set_cache('health', result)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.get('/api/training-load')
def training_load():
    row = get_cache('training_load')
    if row and (time.time() - row[1]) < 30 * 60:
        return jsonify(row[0])
    try:
        client = get_garmin()
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
        set_cache('training_load', result)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.post('/api/sync')
def sync():
    try:
        client = get_garmin()
        acts = client.get_activities(0, 50)
        save_activities(acts)
        clear_cache('health', 'analysis')
        return jsonify({'ok': True, 'count': len(acts)})
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
    elif iso_week <= 25: phase = 'intervallfas'
    elif iso_week <= 29: phase = 'tröskelsfas'
    else:                phase = 'spetsningsfas'

    # Planerat km per vecka (från träningsplanen)
    weekly_km_plan = {
        23:28, 24:44, 25:47, 26:48, 27:49, 28:48,
        29:38, 30:46, 31:43, 32:40, 33:30, 34:20
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
    tl_row = get_cache('training_load')
    tl     = tl_row[0] if tl_row else {}
    acute   = tl.get('acute')
    chronic = tl.get('chronic')
    ratio   = tl.get('ratio')
    acwr_status = tl.get('acwrStatus', '')
    load_feedback = tl.get('loadBalanceFeedback', '')

    # Hälsodata från cache
    h_row = get_cache('health')
    h     = h_row[0] if h_row else {}
    readiness    = (h.get('readiness') or {}).get('score')
    hrv_avg      = (h.get('hrv') or {}).get('lastNightAvg')
    hrv_weekly   = (h.get('hrv') or {}).get('weeklyAvg')
    body_battery = (h.get('bodyBattery') or {}).get('current')
    sleep_score  = (h.get('sleep') or {}).get('score')

    # Google Calendar — kommande 7 dagar
    cal_row = get_cache('gcal_events')
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
                time_str = ev_dt.strftime('%H:%M') if 'T' in start_str else 'heldag'
                desc_str = f" — {ev['desc']}" if ev.get('desc') else ''
                gcal_lines.append(f"- {day_name}: {ev.get('title','')} ({time_str}){desc_str}")
                if ev_dt.hour < 7:
                    early_days.append(day_name)

    # Bygg prompten
    prompt = f"""Du är en personlig träningscoach. Analysera ALL data nedan och svara ENDAST med JSON.

LÖPMÅL: 3 km under 10:00 (bästa: 10:27, gap 27 sek) — deadline slutet aug 2026
VO2max: 59 · Plan: V23 återhämtning → V24-25 intervaller → V26-29 tröskel → V30-34 spetsning
Nuvarande fas: {phase} (V{iso_week})

SENASTE LÖPPASS:
{json.dumps(recent_runs, ensure_ascii=False, indent=2)}

VECKOSTATUS V{iso_week}:
- Planerat: {planned_km} km · Genomfört: {completed_km:.1f} km · Kvar: {remaining_km:.1f} km
- Genomförd träningsload veckan: {round(completed_load)}

HÄLSODATA (idag):
- Träningsberedskap: {readiness or '—'}/100
- HRV natt: {hrv_avg or '—'} ms (veckosnitt {hrv_weekly or '—'} ms)
- Body battery: {body_battery or '—'}/100
- Sömnpoäng: {sleep_score or '—'}/100"""

    # CNS-score beräkning (Flatt & Esco 2016)
    if all(v is not None for v in [readiness, hrv_avg, hrv_weekly, sleep_score, h.get('stress',{}).get('avg')]):
        hrv_pct_val = round((hrv_avg / hrv_weekly) * 100) if hrv_weekly else 50
        stress_avg  = h.get('stress', {}).get('avg', 50) or 50
        cns = round(0.40 * min(hrv_pct_val,100) + 0.30 * (sleep_score or 50) + 0.20 * (readiness or 50) + 0.10 * (100 - min(stress_avg,100)))
        hrv_diff = ((hrv_avg - hrv_weekly) / hrv_weekly * 100) if hrv_weekly else 0
        hrv_signal = 'GRÖN (kör hårt pass)' if hrv_diff >= 5 else 'RÖD (vila/Z2)' if hrv_diff <= -5 else 'GUL (normalt pass)'
        cns_rule = 'KVALITETSPASS OK' if cns >= 70 else 'NORMALT/LÄTT PASS' if cns >= 45 else 'VILA ELLER Z2 — obligatoriskt'
        # Djupsömn och REM flags
        deep_pct = h.get('sleep', {}).get('deepPct', 0) or 0
        rem_pct  = h.get('sleep', {}).get('remPct', 0) or 0
        sleep_flags = []
        if deep_pct < 10: sleep_flags.append('för lite djupsömn (skippa styrka)')
        if rem_pct < 15:  sleep_flags.append('för lite REM (undvik intervaller)')
        prompt += f"""

CNS-SCORE: {cns}/100 — {cns_rule}
HRV-SIGNAL: {hrv_signal} (HRV {hrv_diff:+.0f}% vs veckosnitt)
SÖMNKVALITET: djupsömn {deep_pct}% (mål 15–25%) · REM {rem_pct}% (mål 20–25%){(' · VARNING: ' + ', '.join(sleep_flags)) if sleep_flags else ' · Ok'}
PASSREGEL: CNS ≥70 → kvalitetspass · CNS 45–69 → normalt/lätt · CNS <45 → vila/Z2 obligatoriskt"""

    if acute is not None:
        load_feedback_sv = {
            'AEROBIC_LOW_SHORTAGE':  'för lite lågintensiv aerob träning',
            'AEROBIC_HIGH_SHORTAGE': 'för lite högintensiv aerob träning',
            'ANAEROBIC_SHORTAGE':    'för lite anaerob träning',
            'OPTIMAL':               'optimal balans',
        }.get(load_feedback, load_feedback)
        acwr_sv = {'LOW':'låg','OPTIMAL':'optimal','HIGH':'hög','VERY_HIGH':'mycket hög'}.get(acwr_status, acwr_status)
        prompt += f"""

TRÄNINGSLAST (ACWR):
- Akut last (7 dagar): {acute} · Kronisk last (28 dagar): {chronic}
- ACWR-kvot: {ratio} ({acwr_sv}) — optimal zon är 0.8–1.3
- Belastningsbalans: {load_feedback_sv}
REGEL: Om ACWR < 0.8 kan du öka intensiteten försiktigt. Om > 1.3, prioritera vila eller Z2."""

    if gcal_lines:
        prompt += f"""

ARBETS- OCH AKTIVITETSSCHEMA (kommande 7 dagar):
{chr(10).join(gcal_lines)}
Anpassa rekommendationen — undvik hårda pass på tunga arbetsdagar."""

    if early_days:
        prompt += f"\nTidiga arbetspass (före 07:00, trolig sömnbrist): {', '.join(early_days)} — undvik kvalitetspass dessa dagar och dagen efter."

    prompt += """

Svara ENDAST med detta JSON (inga förklaringar utanför JSON):
{
  "todayRecommendation": "konkret rekommendation för idag baserad på ALL data ovan (1-2 meningar)",
  "todayType": "easy|quality|rest",
  "nextSession": {"title": "passnamn", "desc": "beskrivning", "tempo": "t.ex. 3:35 /km", "distance": "t.ex. ~8 km"},
  "prediction3k": "t.ex. 10:15",
  "insight": "en konkret insikt baserad på träningslasten eller hälsodata (1 mening)"
}"""
    return prompt

@app.post('/api/refresh')
def refresh():
    row = get_cache('analysis')
    if row and (time.time() - row[1]) < 5 * 60:
        return jsonify(row[0])

    try:
        client = get_garmin()
        acts = client.get_activities(0, 10)
        save_activities(acts)
    except Exception as e:
        return jsonify({'error': 'Garmin-fel: ' + str(e)}), 500

    if not ANTHROPIC_KEY or ANTHROPIC_KEY.startswith('sk-ant-placeholder'):
        return jsonify({'todayRecommendation': 'Lägg till Anthropic API-nyckel i .env.',
                        'todayType': 'easy',
                        'nextSession': {'title': 'Lugnt jogg', 'desc': 'Z2, 30-40 min', 'tempo': '4:45-5:15 /km', 'distance': '~6 km'},
                        'prediction3k': '10:27', 'insight': 'AI-insikter kräver API-nyckel.'})

    prompt = _build_refresh_prompt(acts)
    resp = requests.post('https://api.anthropic.com/v1/messages',
        json={'model': 'claude-sonnet-4-6', 'max_tokens': 600,
              'messages': [{'role': 'user', 'content': prompt}]},
        headers={'x-api-key': ANTHROPIC_KEY, 'anthropic-version': '2023-06-01',
                 'content-type': 'application/json'})

    text = resp.json()['content'][0]['text'].strip().replace('```json','').replace('```','').strip()
    analysis = json.loads(text)
    set_cache('analysis', analysis)
    return jsonify(analysis)

@app.post('/api/chat')
def chat():
    data = request.json or {}
    if not ANTHROPIC_KEY:
        return jsonify({'reply': 'API-nyckel saknas.'})
    resp = requests.post('https://api.anthropic.com/v1/messages',
        json={'model': 'claude-sonnet-4-6', 'max_tokens': 1024,
              'system': data.get('context', 'Du är en personlig träningscoach. Svara på svenska.'),
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
    creds = None
    if os.path.exists(GCAL_TOKEN):
        creds = Credentials.from_authorized_user_file(GCAL_TOKEN, GCAL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GRequest())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(GCAL_CREDS, GCAL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(GCAL_TOKEN, 'w') as f:
            f.write(creds.to_json())
    return gbuild('calendar', 'v3', credentials=creds)

def fetch_gcal_events(days=14):
    svc = get_gcal_service()
    if not svc:
        return []
    now = datetime.utcnow()
    time_min = now.isoformat() + 'Z'
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
                'title':    e.get('summary', 'Händelse'),
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
        return jsonify({'ok': False, 'error': 'google_credentials.json saknas', 'events': []})
    events = fetch_gcal_events(days=90)
    # Cacha i DB i 30 min
    set_cache('gcal_events', events)
    return jsonify({'ok': True, 'events': events})

@app.get('/api/calendar/status')
def calendar_status():
    has_creds = os.path.exists(GCAL_CREDS)
    has_token = os.path.exists(GCAL_TOKEN)
    return jsonify({'hasCreds': has_creds, 'hasToken': has_token, 'available': GCAL_AVAILABLE})

# --- Minne / Noteringar ---
@app.get('/api/notes')
def get_notes():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id, text, category, created_at FROM user_notes ORDER BY created_at DESC')
            rows = cur.fetchall()
    return jsonify({'notes': [{'id': r[0], 'text': r[1], 'category': r[2], 'created_at': r[3]} for r in rows]})

@app.post('/api/notes')
def add_note():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get('text', '').strip()
    category = data.get('category', 'general')
    if not text:
        return jsonify({'error': 'Tom notering'}), 400
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO user_notes (text, category, created_at) VALUES (%s, %s, %s) RETURNING id',
                        (text, category, time.time()))
            new_id = cur.fetchone()[0]
        conn.commit()
    return jsonify({'ok': True, 'id': new_id})

@app.delete('/api/notes/<int:note_id>')
def delete_note(note_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM user_notes WHERE id=%s', (note_id,))
        conn.commit()
    return jsonify({'ok': True})

# --- Styrka ---
STRENGTH_TYPES = ('strength_training', 'fitness_equipment', 'gym', 'indoor_cardio', 'cardio', 'bouldering')

@app.get('/api/strength')
def strength_sessions():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT raw FROM activities WHERE type = ANY(%s) ORDER BY date DESC LIMIT 30",
                        (list(STRENGTH_TYPES),))
            rows = cur.fetchall()
    sessions = []
    for r in rows:
        a = r[0]
        sessions.append({
            'id': str(a.get('activityId')),
            'name': a.get('activityName', 'Styrkepass'),
            'date': a.get('startTimeLocal'),
            'duration': a.get('duration'),
            'calories': a.get('calories'),
            'avgHR': a.get('averageHR'),
            'type': a.get('activityType', {}).get('typeKey'),
        })
    return jsonify({'sessions': sessions})

@app.get('/api/strength/<session_id>/exercises')
def get_exercises(session_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id, exercise, sets, reps, weight, note FROM strength_exercises WHERE session_id=%s ORDER BY id',
                        (session_id,))
            rows = cur.fetchall()
    return jsonify({'exercises': [{'id': r[0], 'exercise': r[1], 'sets': r[2], 'reps': r[3], 'weight': r[4], 'note': r[5]} for r in rows]})

@app.post('/api/strength/<session_id>/exercises')
def add_exercise(session_id):
    data = request.get_json(force=True, silent=True) or {}
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO strength_exercises (session_id,exercise,sets,reps,weight,note,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                        (session_id, data.get('exercise',''), data.get('sets'), data.get('reps',''),
                         data.get('weight'), data.get('note',''), time.time()))
            new_id = cur.fetchone()[0]
        conn.commit()
    return jsonify({'ok': True, 'id': new_id})

@app.delete('/api/strength/exercises/<int:ex_id>')
def delete_exercise(ex_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM strength_exercises WHERE id=%s', (ex_id,))
        conn.commit()
    return jsonify({'ok': True})

# --- Statiska filer ---
# ─────────────────────────────────────────────
# TRÄNINGSPLAN — seed-data (samma som JS-arrayen)
# ─────────────────────────────────────────────
PLAN_SEED = [
    # V23 Återhämtning
    {'week':23,'dow':1,'type':'run', 'km':6,  'title':'Återhämtningsjogg',      'detail':'Z2 · 6 km · 4:45–5:15/km · Vila efter GöteborgsVarvet'},
    {'week':23,'dow':2,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',         'detail':'Z2 · Lugnt tempo · 5:00–5:20/km · Aktiv återhämtning'},
    {'week':23,'dow':3,'type':'lift','km':0,  'title':'Helkropp – intro',        'detail':'Knäböj, marklyft, bänkpress, latsdrag · 3×8 · 60–70%'},
    {'week':23,'dow':4,'type':'easy','km':5,  'title':'Lätt Z2 · 5 km',         'detail':'Z2 · Kort och lätt · Spola ur benen'},
    {'week':23,'dow':6,'type':'easy','km':10, 'title':'Söndagsjogg · 10 km',     'detail':'Z2 · 5:00–5:20/km · Veckoavslutet'},
    # V24 Bas
    {'week':24,'dow':0,'type':'easy','km':6,  'title':'Lätt Z2 · 6 km',         'detail':'Z2 · Aktivering inför veckans kvalitetspass'},
    {'week':24,'dow':1,'type':'run', 'km':8,  'title':'4×1000m intervaller',     'detail':'Z3–Z4 · 3:35/km · 3 min vila · ~8 km totalt'},
    {'week':24,'dow':2,'type':'easy','km':9,  'title':'Lätt Z2 · 9 km',         'detail':'Z2 · 5:00–5:15/km · Aktivt vilodygn'},
    {'week':24,'dow':3,'type':'lift','km':0,  'title':'Underkropp + core',       'detail':'Knäböj, RDL, benpress, split-squat, plankan · 3–4 set'},
    {'week':24,'dow':4,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',         'detail':'Z2 · Inför lördagets bansprint'},
    {'week':24,'dow':5,'type':'run', 'km':6,  'title':'6×400m snabba drag',      'detail':'Z5 · 3:10/km · 90 sek vila · Bana'},
    {'week':24,'dow':6,'type':'easy','km':10, 'title':'Söndagsjogg · 10 km',     'detail':'Z2 · Aktiv återhämtning efter banpasset'},
    # V25 Bas
    {'week':25,'dow':0,'type':'easy','km':5,  'title':'Lätt Z2 · 5 km',         'detail':'Z2 · Kort aktivering'},
    {'week':25,'dow':1,'type':'easy','km':9,  'title':'Medium Z2 · 9 km',       'detail':'Z2 · 5:00/km · Aerob bas'},
    {'week':25,'dow':2,'type':'run', 'km':10, 'title':'5×1000m tröskel',         'detail':'Z3–Z4 · 3:35/km · 2:30 min vila · ~10 km totalt'},
    {'week':25,'dow':3,'type':'lift','km':0,  'title':'Överkropp',               'detail':'Bänkpress, axelpress, latsdrag, rodd · 3–4 set'},
    {'week':25,'dow':4,'type':'easy','km':6,  'title':'Lätt Z2 · 6 km',         'detail':'Z2 · Återhämtning'},
    {'week':25,'dow':5,'type':'run', 'km':12, 'title':'Långpass · 12 km',        'detail':'Z2 · 5:00–5:20/km'},
    {'week':25,'dow':6,'type':'easy','km':5,  'title':'Lätt avslutning · 5 km', 'detail':'Z2 · Söndagsjogg'},
    # V26 Tröskel
    {'week':26,'dow':0,'type':'easy','km':6,  'title':'Lätt Z2 · 6 km',         'detail':'Z2 · Aktivering'},
    {'week':26,'dow':1,'type':'run', 'km':10, 'title':'3×2000m tröskel',         'detail':'Z4 · 3:35/km · 3 min vila · ~10 km'},
    {'week':26,'dow':2,'type':'easy','km':9,  'title':'Medium Z2 · 9 km',       'detail':'Z2 · Aerob bas'},
    {'week':26,'dow':3,'type':'lift','km':0,  'title':'Underkropp – tung',       'detail':'Knäböj, marklyft, bulgarska · 4×6–8 · 80%'},
    {'week':26,'dow':4,'type':'easy','km':6,  'title':'Lätt Z2 · 6 km',         'detail':'Z2 · Inför fartlekpass'},
    {'week':26,'dow':5,'type':'run', 'km':8,  'title':'8×400m fartlek',          'detail':'Z4–Z5 · 90 sek vila · ~8 km'},
    {'week':26,'dow':6,'type':'easy','km':10, 'title':'Söndagsjogg · 10 km',     'detail':'Z2'},
    # V27 Tröskel
    {'week':27,'dow':0,'type':'easy','km':6,  'title':'Lätt Z2 · 6 km',         'detail':'Z2'},
    {'week':27,'dow':1,'type':'run', 'km':10, 'title':'2×3000m @ 3:35/km',      'detail':'Z4 · 4 min vila · ~10 km'},
    {'week':27,'dow':2,'type':'easy','km':9,  'title':'Medium Z2 · 9 km',       'detail':'Z2 · Aktiv återhämtning'},
    {'week':27,'dow':3,'type':'lift','km':0,  'title':'Överkropp – tung',        'detail':'Bänkpress, axelpress, dips, chins · 4×6 · 80%'},
    {'week':27,'dow':4,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',         'detail':'Z2'},
    {'week':27,'dow':5,'type':'run', 'km':12, 'title':'Långpass · 12 km',        'detail':'Z2 · 5:00/km'},
    {'week':27,'dow':6,'type':'easy','km':6,  'title':'Lätt avslutning · 6 km', 'detail':'Z2'},
    # V28 Tröskel
    {'week':28,'dow':0,'type':'easy','km':6,  'title':'Lätt Z2 · 6 km',         'detail':'Z2'},
    {'week':28,'dow':1,'type':'run', 'km':10, 'title':'5×1000m tröskel',         'detail':'Z4 · 3:33/km · Ökad intensitet · ~10 km'},
    {'week':28,'dow':2,'type':'easy','km':9,  'title':'Medium Z2 · 9 km',       'detail':'Z2 · 5:00/km'},
    {'week':28,'dow':3,'type':'lift','km':0,  'title':'Underkropp – tung',       'detail':'Knäböj, RDL, benpress · 4×5 · 82%'},
    {'week':28,'dow':4,'type':'easy','km':6,  'title':'Lätt Z2 · 6 km',         'detail':'Z2'},
    {'week':28,'dow':5,'type':'run', 'km':7,  'title':'6×500m sharpening',       'detail':'Z5 · 3:12/km · 90 sek vila · ~7 km'},
    {'week':28,'dow':6,'type':'easy','km':10, 'title':'Söndagsjogg · 10 km',     'detail':'Z2'},
    # V29 Kontrolltest
    {'week':29,'dow':0,'type':'easy','km':5,  'title':'Lätt Z2 · 5 km',         'detail':'Z2 · Spara benen'},
    {'week':29,'dow':1,'type':'run', 'km':8,  'title':'Lätt tröskelpass',        'detail':'2×2000m · Z4 · 3 min vila · ~8 km'},
    {'week':29,'dow':2,'type':'easy','km':7,  'title':'Medium Z2 · 7 km',       'detail':'Z2'},
    {'week':29,'dow':3,'type':'lift','km':0,  'title':'Lätt styrka',             'detail':'3 övningar · 3×6 · 75%'},
    {'week':29,'dow':4,'type':'easy','km':5,  'title':'Lätt jogg · 5 km',       'detail':'Z2 · Aktivering dagen innan test'},
    {'week':29,'dow':5,'type':'race','km':7,  'title':'⭐ 3 km KONTROLLTEST',   'detail':'Uppvärmning 2 km + 3 km test (mål <10:10) + nedvarvning 2 km'},
    {'week':29,'dow':6,'type':'easy','km':6,  'title':'Återhämtningsjogg · 6 km','detail':'Z2 · Lätt efter gårdagens test'},
    # V30 Spetsning
    {'week':30,'dow':0,'type':'easy','km':6,  'title':'Lätt Z2 · 6 km',         'detail':'Z2'},
    {'week':30,'dow':1,'type':'run', 'km':7,  'title':'6×500m sharpening',       'detail':'Z5 · 3:10/km · 2 min vila · ~7 km'},
    {'week':30,'dow':2,'type':'easy','km':9,  'title':'Medium Z2 · 9 km',       'detail':'Z2 · Aerob bas'},
    {'week':30,'dow':3,'type':'lift','km':0,  'title':'Underhållsstyrka',        'detail':'3 övningar · 3×5 · 85%'},
    {'week':30,'dow':4,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',         'detail':'Z2'},
    {'week':30,'dow':5,'type':'run', 'km':8,  'title':'Lätt fartlek',            'detail':'Z2 med 4×1 min snabba drag · ~8 km'},
    {'week':30,'dow':6,'type':'easy','km':9,  'title':'Söndagsjogg · 9 km',      'detail':'Z2'},
    # V31 Spetsning
    {'week':31,'dow':0,'type':'easy','km':6,  'title':'Lätt Z2 · 6 km',         'detail':'Z2'},
    {'week':31,'dow':1,'type':'run', 'km':7,  'title':'6×500m sharpening',       'detail':'Z5 · 3:08/km · ~7 km'},
    {'week':31,'dow':2,'type':'easy','km':8,  'title':'Medium Z2 · 8 km',       'detail':'Z2 · 5:00/km'},
    {'week':31,'dow':3,'type':'lift','km':0,  'title':'Underhållsstyrka',        'detail':'3 övningar · 3×5 · 85%'},
    {'week':31,'dow':4,'type':'easy','km':6,  'title':'Lätt Z2 · 6 km',         'detail':'Z2'},
    {'week':31,'dow':5,'type':'easy','km':8,  'title':'Mellanlångt Z2 · 8 km',  'detail':'Z2 · Sista längre pass i spetsningsfasen'},
    {'week':31,'dow':6,'type':'easy','km':8,  'title':'Söndagsjogg · 8 km',      'detail':'Z2'},
    # V32 Avtrappning
    {'week':32,'dow':0,'type':'easy','km':5,  'title':'Lätt Z2 · 5 km',         'detail':'Z2'},
    {'week':32,'dow':1,'type':'run', 'km':8,  'title':'4×1000m tävlingsfart',    'detail':'Z4–Z5 · 3:19/km · 3 min vila · ~8 km'},
    {'week':32,'dow':2,'type':'easy','km':8,  'title':'Medium Z2 · 8 km',       'detail':'Z2 · Aktiv återhämtning'},
    {'week':32,'dow':3,'type':'lift','km':0,  'title':'Underhållsstyrka',        'detail':'3 övningar · 3×5 · 85%'},
    {'week':32,'dow':4,'type':'easy','km':5,  'title':'Lätt Z2 · 5 km',         'detail':'Z2'},
    {'week':32,'dow':5,'type':'run', 'km':6,  'title':'Lätt jogg + strides',     'detail':'25 min Z2 + 6×80m strides'},
    {'week':32,'dow':6,'type':'easy','km':8,  'title':'Söndagsjogg · 8 km',      'detail':'Z2'},
    # V33 Nedtrappning
    {'week':33,'dow':0,'type':'easy','km':5,  'title':'Lätt Z2 · 5 km',         'detail':'Z2 · Spara benen'},
    {'week':33,'dow':1,'type':'run', 'km':7,  'title':'3×1000m tävlingsfart',    'detail':'Z5 · 3:15–3:19/km · 4 min vila · ~7 km'},
    {'week':33,'dow':2,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',         'detail':'Z2'},
    {'week':33,'dow':3,'type':'lift','km':0,  'title':'Kort underhållsstyrka',   'detail':'2 övningar · 2×5 · 80%'},
    {'week':33,'dow':4,'type':'easy','km':5,  'title':'Lätt jogg · 5 km',       'detail':'Z2 · 20 min'},
    {'week':33,'dow':6,'type':'easy','km':6,  'title':'Söndagsjogg · 6 km',      'detail':'Z2 · Lugn avslutning'},
    # V34 Tävlingsvecka
    {'week':34,'dow':0,'type':'easy','km':5,  'title':'Lätt aktivering · 5 km', 'detail':'Z2 · 15–20 min'},
    {'week':34,'dow':1,'type':'easy','km':5,  'title':'Strides · 5 km',         'detail':'10 min Z2 + 4×80m strides'},
    {'week':34,'dow':2,'type':'rest','km':0,  'title':'Vila',                    'detail':'Fullständig vila. Ät bra, sov länge, visualisera loppet.'},
    {'week':34,'dow':3,'type':'race','km':10, 'title':'🏆 3 KM — SUB 10:00',    'detail':'Uppvärm 3 km · UT: 3:22/km · Km 2: 3:20 · Km 3: 3:15 · MÅL: 9:59!'},
]

def seed_plan():
    """Fyll plan_sessions från PLAN_SEED om tabellen är tom."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM plan_sessions')
            if cur.fetchone()[0] > 0:
                return  # redan seedat
            for s in PLAN_SEED:
                cur.execute('''INSERT INTO plan_sessions
                    (week, dow, type, km, title, detail, status, original_week, original_dow)
                    VALUES (%s,%s,%s,%s,%s,%s,'planned',%s,%s)''',
                    (s['week'], s['dow'], s['type'], s['km'],
                     s['title'], s['detail'], s['week'], s['dow']))
        conn.commit()
    print(f'Plan seedat: {len(PLAN_SEED)} pass')

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
            cur.execute('SELECT * FROM plan_sessions ORDER BY week, dow')
            rows = cur.fetchall()
    return jsonify({'sessions': [dict(r) for r in rows]})

@app.patch('/api/plan/<int:session_id>')
def update_session(session_id):
    data = request.json or {}
    allowed = {'status','week','dow','title','detail','km','ai_note'}
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({'error': 'Inga giltiga fält'}), 400
    fields['modified_at'] = time.time()
    set_clause = ', '.join(f'{k} = %s' for k in fields)
    vals = list(fields.values()) + [session_id]
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(f'UPDATE plan_sessions SET {set_clause} WHERE id = %s', vals)
        conn.commit()
    return jsonify({'ok': True})


# ─────────────────────────────────────────────
# AKTIVITETSMATCHNING
# ─────────────────────────────────────────────
def _iso_week_dow(d):
    """Returnera (iso_week, dow_0mon) för ett date-objekt."""
    iso = d.isocalendar()
    return iso[1], iso[2] - 1  # dow: 0=mån

def match_activities_to_plan():
    """
    Jämför Garmin-aktiviteter mot planerade pass.
    Markerar pass som completed eller missed.
    Körs varje morgon innan AI-justeraren.
    """
    today = date.today()
    yesterday = today - timedelta(days=1)
    y_week, y_dow = _iso_week_dow(yesterday)

    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Hämta gårdagens planerade pass som fortfarande är 'planned'
            cur.execute('''SELECT * FROM plan_sessions
                WHERE week = %s AND dow = %s AND status = 'planned' ''',
                (y_week, y_dow))
            planned = cur.fetchall()
            if not planned:
                return

            # Hämta Garmin-aktiviteter från igår
            cur.execute('''SELECT raw FROM activities
                WHERE date >= %s AND date < %s''',
                (yesterday.isoformat(), today.isoformat()))
            acts = [r['raw'] for r in cur.fetchall()]

        run_types = {'running','track_running','treadmill_running','trail_running'}
        did_run    = any(a.get('activityType',{}).get('typeKey','') in run_types for a in acts)
        did_lift   = any(a.get('activityType',{}).get('typeKey','') in
                        {'strength_training','fitness_equipment'} for a in acts)

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

                new_status = 'completed' if completed else 'missed'
                cur.execute('''UPDATE plan_sessions SET status = %s, modified_at = %s
                    WHERE id = %s''', (new_status, time.time(), p['id']))
        conn.commit()
    print(f'Aktivitetsmatchning klar för {yesterday}')


# ─────────────────────────────────────────────
# AI-JUSTERARE
# ─────────────────────────────────────────────
def ai_adjust_plan():
    """
    Kärnan i den automatiska planjusteringen.
    Körs kl 07:30 varje morgon efter sömndata kommit in.
    """
    if not ANTHROPIC_KEY:
        print('AI-justering: API-nyckel saknas')
        return

    today     = date.today()
    iso_week  = today.isocalendar()[1]

    # 1. Synka Garmin och hälsodata
    try:
        client = get_garmin()
        acts = client.get_activities(0, 20)
        save_activities(acts)
        # Rensa hälso-cache så färsk sömndata hämtas
        clear_cache('health', 'training_load')
    except Exception as e:
        print('AI-justering: Garmin-fel', e)

    # 2. Hämta hälsodata
    try:
        client = get_garmin()
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
        print('AI-justering: hälsodata-fel', e)
        sleep_score = deep_pct = rem_pct = total_h = None
        ready_score = hrv_avg = hrv_weekly = hrv_pct = None
        acute = chronic = acwr = None

    # 3. Hämta missade pass + kommande 14 dagar
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('''SELECT * FROM plan_sessions
                WHERE status = 'missed' AND week >= %s
                ORDER BY week, dow''', (iso_week - 1,))
            missed = [dict(r) for r in cur.fetchall()]

            cur.execute('''SELECT * FROM plan_sessions
                WHERE status = 'planned' AND week >= %s
                ORDER BY week, dow LIMIT 20''', (iso_week,))
            upcoming = [dict(r) for r in cur.fetchall()]

            # Genomförd km och load denna vecka
            cur.execute('''SELECT raw FROM activities WHERE date >= %s''',
                ((today - timedelta(days=today.weekday())).isoformat(),))
            week_acts = [r['raw'] for r in cur.fetchall()]

    completed_km   = sum((a.get('distance',0) or 0)/1000 for a in week_acts
                         if any(t in (a.get('activityType',{}).get('typeKey',''))
                                for t in ('running','track_running','treadmill_running','trail_running')))
    completed_load = sum(a.get('activityTrainingLoad',0) or 0 for a in week_acts)

    weekly_km_plan = {23:28,24:44,25:47,26:48,27:49,28:48,29:38,30:46,31:43,32:40,33:30,34:20}
    planned_km = weekly_km_plan.get(iso_week, 40)
    week_cap   = round(planned_km * 1.1)

    # 4. Google Calendar — hämta från cache
    cal_row = get_cache('gcal_events')
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
    prompt = f"""Du är en intelligent träningsplanerare. Din uppgift är att justera träningsschemat för en löpare baserat på aktuell hälsodata.

MÅL: 3 km under 10:00 (bästa: 10:27) — deadline slutet aug 2026
DAGENS DATUM: {today} (V{iso_week})

SÖMNDATA IDAG:
- Sömnpoäng: {sleep_score or '—'}/100 · Total sömn: {total_h or '—'} h
- Djupsömn: {deep_pct or '—'}% (mål 15–25%) · REM: {rem_pct or '—'}% (mål 20–25%)
- Garmin beredskap: {ready_score or '—'}/100
- HRV natt: {hrv_avg or '—'} ms ({hrv_pct or '—'}% av veckosnitt {hrv_weekly or '—'} ms)

TRÄNINGSLAST (ACWR):
- Akut: {acute or '—'} · Kronisk: {chronic or '—'} · Kvot: {acwr or '—'}
- Optimal zon: 0.8–1.3. Om ombokning driver kvoten >1.3 → ersätt med Z2 eller hoppa.

VECKOSTATUS V{iso_week}:
- Genomfört: {completed_km:.1f} km · Veckans tak: {week_cap} km
- Genomförd load: {round(completed_load)}

MISSADE PASS (behöver beslutas):
{json.dumps(missed, ensure_ascii=False, indent=2) if missed else '(inga missade pass)'}

KOMMANDE PLANERADE PASS (nästa 14 dagar):
{json.dumps([{'id':s['id'],'week':s['week'],'dow':s['dow'],'type':s['type'],'km':s['km'],'title':s['title']} for s in upcoming], ensure_ascii=False, indent=2)}

GOOGLE KALENDER (kommande 14 dagar):
{gcal_str or '(inga events)'}

REGLER:
1. Flytta ett missat kvalitetspass (run/race) till närmast lediga dag om ACWR-kvoten tillåter det (<1.3)
2. Flytta INTE till en dag som redan har ett annat kvalitetspass
3. Om veckans km-tak är nått → ersätt med Z2 eller markera som 'skipped'
4. Missad styrka → flytta till närmast lediga dag (helst ej dagen efter ett hårt löppass)
5. Om djupsömn <10% eller REM <15% → undvik hårda pass idag (V{iso_week}, dag {today.weekday()})
6. Beakta Google Calendar — undvik hårda pass på tunga arbetsdagar

Svara ENDAST med detta JSON (inga kommentarer utanför):
{{
  "changes": [
    {{
      "session_id": <int>,
      "action": "reschedule|skip|keep",
      "new_week": <int eller null>,
      "new_dow": <int 0-6 eller null>,
      "reason": "<kort motivering på svenska>"
    }}
  ],
  "summary": "<en mening som sammanfattar vad som justerades>"
}}"""

    # 6. Anropa Claude
    try:
        resp = requests.post('https://api.anthropic.com/v1/messages',
            json={'model': 'claude-sonnet-4-6', 'max_tokens': 1000,
                  'messages': [{'role': 'user', 'content': prompt}]},
            headers={'x-api-key': ANTHROPIC_KEY, 'anthropic-version': '2023-06-01',
                     'content-type': 'application/json'})
        text = resp.json()['content'][0]['text'].strip().replace('```json','').replace('```','').strip()
        result = json.loads(text)
    except Exception as e:
        print('AI-justering: Claude-fel', e)
        return

    # 7. Applicera ändringarna på DB
    changes_applied = 0
    with db() as conn:
        with conn.cursor() as cur:
            for change in result.get('changes', []):
                sid    = change.get('session_id')
                action = change.get('action')
                if not sid or action == 'keep':
                    continue
                if action == 'skip':
                    cur.execute('''UPDATE plan_sessions
                        SET status='skipped', ai_note=%s, modified_at=%s WHERE id=%s''',
                        (change.get('reason',''), time.time(), sid))
                    changes_applied += 1
                elif action == 'reschedule':
                    new_week = change.get('new_week')
                    new_dow  = change.get('new_dow')
                    if new_week and new_dow is not None:
                        cur.execute('''UPDATE plan_sessions
                            SET status='planned', week=%s, dow=%s,
                                ai_note=%s, modified_at=%s WHERE id=%s''',
                            (new_week, new_dow, change.get('reason',''), time.time(), sid))
                        changes_applied += 1
        conn.commit()

    summary = result.get('summary', '')
    print(f'AI-justering klar: {changes_applied} ändringar. {summary}')
    set_cache('last_plan_adjustment', {
        'date': today.isoformat(),
        'changes': changes_applied,
        'summary': summary
    })


# ─────────────────────────────────────────────
# MANUELL TRIGGER (för testning)
# ─────────────────────────────────────────────
@app.post('/api/plan/adjust')
def manual_adjust():
    """Trigga AI-justeringen manuellt (t.ex. för testning)."""
    try:
        match_activities_to_plan()
        ai_adjust_plan()
        row = get_cache('last_plan_adjustment')
        return jsonify({'ok': True, 'result': row[0] if row else {}})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.get('/api/plan/status')
def plan_status():
    """Senaste AI-justeringens status."""
    row = get_cache('last_plan_adjustment')
    return jsonify(row[0] if row else {'date': None, 'changes': 0, 'summary': ''})


# ─────────────────────────────────────────────
# SCHEDULER — kör kl 07:30 varje morgon
# ─────────────────────────────────────────────
def morning_job():
    print(f'[{datetime.now().strftime("%H:%M")}] Morgonrutin startar...')
    match_activities_to_plan()
    ai_adjust_plan()

scheduler = BackgroundScheduler(timezone='Europe/Stockholm')
scheduler.add_job(morning_job, 'cron', hour=7, minute=30)
scheduler.start()
print('Schemaläggare aktiv — AI-justering körs kl 07:30 varje morgon')


@app.get('/')
def index():
    return send_from_directory('public', 'index.html')

@app.get('/<path:path>')
def static_files(path):
    return send_from_directory('public', path)

if __name__ == '__main__':
    print('Dashboard startar på http://localhost:3000')
    print('Tryck Ctrl+C för att stänga')
    app.run(port=3000, debug=False)
