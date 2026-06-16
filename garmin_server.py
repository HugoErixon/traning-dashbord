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
AC_KEEPER_URL = config.get('AC_KEEPER_URL', 'http://127.0.0.1:8089')

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

@app.get('/api/ac')
def ac_proxy():
    """Hämtar aktuell temperatur/AC-status från ac-keeper (på Pi:n via localhost)."""
    try:
        r = requests.get(f'{AC_KEEPER_URL}/api/current', timeout=4)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'available': False, 'error': str(e)})

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
HRV_STATUS_VERDICT = {     # status → kort verdikt (engelska)
    'BALANCED':   'Balanced — autonomic system in your normal range',
    'UNBALANCED': 'Unbalanced — outside your normal range, train with caution',
    'LOW':        'Low — below baseline, prioritize recovery',
    'POOR':       'Poor — sustained low HRV, rest needed',
    'NONE':       'Not enough baseline data yet',
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

        hrv_sum  = hrv.get('hrvSummary', {})
        hrv_base = hrv_sum.get('baseline') or {}
        hrv_ln   = hrv_sum.get('lastNightAvg')
        hrv_wk   = hrv_sum.get('weeklyAvg')
        hrv_st   = hrv_sum.get('status')
        hrv_pct  = round((hrv_ln / hrv_wk) * 100) if hrv_wk and hrv_ln else None
        hrv_comp = hrv_component(hrv_ln, hrv_base.get('lowUpper'), hrv_base.get('balancedLow'), hrv_st, hrv_pct)
        hrv_lt, hrv_verdict = hrv_signal(hrv_st, hrv_ln, hrv_wk)

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
            'hrv':         {'lastNightAvg': hrv_ln, 'weeklyAvg': hrv_wk, 'status': hrv_st, 'pct': hrv_pct,
                            'balancedLow': hrv_base.get('balancedLow'), 'balancedUpper': hrv_base.get('balancedUpper'),
                            'lowUpper': hrv_base.get('lowUpper'), 'component': hrv_comp,
                            'light': hrv_lt, 'verdict': hrv_verdict},
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
    # Hämta dagens och nästa planerade pass från DB
    today_session = None
    next_session  = None
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT * FROM plan_sessions
                WHERE week=%s AND dow=%s AND status='planned'
                LIMIT 1""", (iso_week, weekday))
            today_session = cur.fetchone()
            cur.execute("""SELECT * FROM plan_sessions
                WHERE status='planned' AND (week > %s OR (week = %s AND dow > %s))
                ORDER BY week, dow LIMIT 1""", (iso_week, iso_week, weekday))
            next_session = cur.fetchone()

    today_session_str = f"{today_session['title']} — {today_session['detail']}" if today_session else "Rest day (no session scheduled)"
    next_session_str  = f"{next_session['title']} — {next_session['detail']}"   if next_session  else "No upcoming session found"

    prompt = f"""You are a personal training coach. Analyze ALL data below and respond ONLY with JSON. All text fields in the JSON must be in English.

GOAL: Half marathon under 1:20 (3:47/km) on October 10, 2026 · Current best: 1:26:19
SECONDARY GOAL: Build a strong body in all areas — running strength, upper body, core, mobility
VO2max: 59 · Plan: W23–41 · Current phase: {phase} (W{iso_week})

TODAY'S SCHEDULED SESSION (from training plan):
{today_session_str}

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
                          'UNBALANCED':'YELLOW (unbalanced — caution)',
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
            cur.execute('SELECT * FROM plan_sessions WHERE week=%s AND dow=%s', (wk, dw))
            planned = cur.fetchall()
        with conn.cursor() as cur:
            cur.execute('''SELECT name, type, distance, duration, avg_hr
                FROM activities WHERE date >= %s ORDER BY date''', (today.isoformat(),))
            act_rows = cur.fetchall()

    planned_str = '; '.join(f"{p['title']} — {p['detail']}" for p in planned) if planned \
                  else 'Rest day (no session scheduled)'

    acts = []
    for name, typ, dist, dur, hr in act_rows:
        parts = [typ or 'activity']
        if dist: parts.append(f"{dist/1000:.1f} km")
        if dur:  parts.append(f"{int(dur/60)} min")
        if dist and dur and dist > 0:
            pace = (dur / 60) / (dist / 1000)  # min/km
            parts.append(f"pace {int(pace)}:{int((pace % 1) * 60):02d}/km")
        if hr: parts.append(f"avgHR {hr}")
        acts.append(f"{name or 'Activity'} ({', '.join(parts)})")
    acts_str = '; '.join(acts) if acts else 'nothing logged yet today'

    # Dagens kalender (jobb/åtaganden) så "har du tid" blir smart
    cal_row = get_cache('gcal_events')
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
- DONE: an activity matching the planned session was completed today. Praise it and compare performance to the plan's target pace/distance using the actual pace shown (e.g. "right on target" or "a bit slower than planned").
- PENDING: the session has not been done yet. Use the current time AND the calendar to judge if there is still time today — if so, reassure ("you still have time, fit it in before/after work"); if it's late evening with no window left, gently note the day is nearly over.
- OTHER: the athlete did something different than planned today — acknowledge it.
- REST: it's a rest day — confirm that resting is the right call.

Respond ONLY with this JSON (all text in English):
{{
  "status": "done | pending | missed | rest | other",
  "headline": "max 6 words",
  "body": "1-3 short, friendly sentences specific to today."
}}"""

@app.get('/api/training-review')
def training_review():
    force = request.args.get('force') == '1'
    row = get_cache('training_review')
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
        set_cache('training_review', review)
        return jsonify(review)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.post('/api/chat')
def chat():
    data = request.json or {}
    if not ANTHROPIC_KEY:
        return jsonify({'reply': 'API-nyckel saknas.'})
    resp = requests.post('https://api.anthropic.com/v1/messages',
        json={'model': 'claude-sonnet-4-6', 'max_tokens': 1024,
              'system': data.get('context', 'You are a personal training coach. Always respond in English.'),
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
    if get_gcal_service() is None:
        return jsonify({'ok': False, 'error': 'Google-token har gått ut eller återkallats. Kör reauth_google.py och logga in igen.', 'events': []})
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

def reseed_plan():
    """Ersätt alla planerade pass med ny PLAN_SEED. Behåller completed/missed/skipped som historik."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM plan_sessions WHERE status = 'planned'")
            for s in PLAN_SEED:
                cur.execute('''INSERT INTO plan_sessions
                    (week, dow, type, km, title, detail, status, original_week, original_dow)
                    VALUES (%s,%s,%s,%s,%s,%s,'planned',%s,%s)''',
                    (s['week'], s['dow'], s['type'], s['km'],
                     s['title'], s['detail'], s['week'], s['dow']))
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

    weekly_km_plan = {23:35,24:40,25:45,26:50,27:55,28:55,29:58,30:62,31:65,32:65,33:60,34:68,35:70,36:68,37:65,38:55,39:50,40:35,41:15}
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
    def _sess(s):
        return {'id': s['id'], 'vecka': s['week'], 'dag': s['dow'], 'typ': s['type'],
                'km': s['km'], 'titel': s['title'], 'detalj': s['detail']}
    missed_json   = json.dumps([_sess(s) for s in missed],   ensure_ascii=False, indent=2) if missed else '(inga missade pass)'
    upcoming_json = json.dumps([_sess(s) for s in upcoming], ensure_ascii=False, indent=2)

    prompt = f"""You are an experienced running coach with deep knowledge of physiology and training planning. You are working with a runner whose goal is a half marathon under 1:20 (3:47/km) on October 10, 2026. Current best: 1:26:19. Secondary goal: build a strong body in all areas — running strength, upper body, core, mobility. The plan runs W23–41 with phases: recovery → base building → threshold/tempo → race-specific → taper. Always respond in English. All JSON text fields must be in English.

DAGENS DATUM: {today} (vecka {iso_week}, dag {today.weekday()} där 0=måndag)

═══ LÖPARENS AKTUELLA STATUS ═══

Sömn idag:
- Poäng: {sleep_score or 'saknas'}/100
- Total: {total_h or 'saknas'} h · Djupsömn: {deep_pct or 'saknas'}% · REM: {rem_pct or 'saknas'}%

Återhämtning:
- Garmin beredskap: {ready_score or 'saknas'}/100
- HRV natt: {hrv_avg or 'saknas'} ms · Veckosnitt: {hrv_weekly or 'saknas'} ms · Avvikelse: {(str(hrv_pct - 100) + '%') if hrv_pct else 'saknas'}

Träningslast (ACWR):
- Akut: {acute or 'saknas'} · Kronisk: {chronic or 'saknas'} · Kvot: {acwr or 'saknas'}
- (Referens: <0.8 undertränad, 0.8–1.3 optimal, >1.3 skaderisk)

Veckostatus V{iso_week}:
- Genomfört löpning: {completed_km:.1f} km · Veckans planerade tak: {week_cap} km
- Genomförd total load: {round(completed_load)}

═══ PASS SOM BEHÖVER BESLUTAS ═══

Missade pass:
{missed_json}

Kommande planerade pass (nästa 14 dagar):
{upcoming_json}

Google Calendar — kommande 14 dagar (påverkar återhämtning och timing):
{gcal_str or '(inga events)'}

═══ DIN UPPGIFT ═══

Analysera situationen som en coach och fatta de bästa besluten för löparens långsiktiga utveckling. Du har full frihet att:

- Flytta pass (reschedule) — ange ny vecka och dag
- Hoppa över pass (skip) — om det inte tillför värde givet tröttheten
- Modifiera passinnehåll (modify) — ändra km, tempo, typ eller struktur
- Kombinera logik — t.ex. flytta OCH ändra innehållet på samma pass
- Lämna pass oförändrade (keep) — om det är rätt beslut

Tänk som en coach, inte som ett regelblad. Exempel på resonemang du bör göra:
- Om tre hårda pass ligger på rad → omfördela för att undvika ackumulerad trötthet
- Om ett pass missats men nästa ändå passar bra strukturmässigt → kanske bättre att göra nästa pass lite längre än att pressa in det missade
- Om löparen är i bra form (hög HRV, god sömn) → utnyttja det, höj en notch
- Om löparen är trött → skydda kvalitetsanpassningarna, hellre ett bra pass än tre halvdåliga
- Beakta Google Calendar — en stressig arbetsdag påverkar återhämtning
- Undvik att stapla mer än 2 hårda pass i rad (löp-kvalitet eller styrka med hög belastning)
- Håll pass som 'completed' eller 'skipped' oförändrade

Skriv ditt resonemang kortfattat i "coaching_notes" innan du presenterar besluten.

Svara ENDAST med detta JSON (inga kommentarer utanför):
{{
  "coaching_notes": "<2–4 meningar om hur du tolkar situationen och varför du väljer som du gör>",
  "changes": [
    {{
      "session_id": <int>,
      "action": "reschedule|skip|keep|modify",
      "new_week": <int eller null>,
      "new_dow": <int 0–6 eller null>,
      "new_km": <float eller null>,
      "new_title": "<sträng eller null>",
      "new_detail": "<fullt uppdaterad passbeskrivning med tempo, distans, instruktioner — eller null om oförändrad>",
      "reason": "<en mening om varför just detta beslut>"
    }}
  ],
  "summary": "<en mening som sammanfattar dagens justeringar>"
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
                                ai_note=%s, modified_at=%s{extra_sql} WHERE id=%s''',
                            [new_week, new_dow, change.get('reason',''), time.time()] + extra_vals + [sid])
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
                    mod_vals.append(sid)
                    cur.execute(f'''UPDATE plan_sessions
                        SET {','.join(mod_sets)} WHERE id=%s AND status='planned' ''',
                        mod_vals)
                    changes_applied += 1
        conn.commit()

    summary        = result.get('summary', '')
    coaching_notes = result.get('coaching_notes', '')
    print(f'AI-justering klar: {changes_applied} ändringar. {summary}')
    if coaching_notes:
        print(f'Coach: {coaching_notes}')
    set_cache('last_plan_adjustment', {
        'date': today.isoformat(),
        'changes': changes_applied,
        'summary': summary,
        'coaching_notes': coaching_notes
    })


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
    return jsonify(row[0] if row else {'date': None, 'changes': 0, 'summary': '', 'coaching_notes': ''})


# ─────────────────────────────────────────────
# SCHEDULER — kör kl 07:30 varje morgon
# ─────────────────────────────────────────────
def morning_job():
    print(f'[{datetime.now().strftime("%H:%M")}] Morgonrutin startar...')
    match_activities_to_plan()
    ai_adjust_plan()

def backup_job():
    """Körs 10:00 — bara om justeringen inte redan gjorts idag."""
    row = get_cache('last_plan_adjustment')
    if row and row[0].get('date') == date.today().isoformat():
        print('[10:00] Justering redan gjord idag, hoppar över.')
        return
    print('[10:00] Ingen justering gjord ännu idag — kör nu.')
    morning_job()

scheduler = BackgroundScheduler(timezone='Europe/Stockholm')
scheduler.add_job(morning_job, 'cron', hour=7, minute=30)
scheduler.add_job(backup_job,  'cron', hour=10, minute=0)
scheduler.start()
print('Schemaläggare aktiv — AI-justering kl 07:30, backup kl 10:00')


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
