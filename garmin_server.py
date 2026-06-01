from flask import Flask, request, jsonify, send_from_directory
from garminconnect import Garmin
from pathlib import Path
from dotenv import dotenv_values
import sqlite3, json, time, requests, os
from datetime import date

app = Flask(__name__, static_folder='public')

config = dotenv_values('.env')
PASSWORD = config.get('SITE_PASSWORD', 'hugo123')
ANTHROPIC_KEY = config.get('ANTHROPIC_API_KEY', '')
TOKEN_DIR = str(Path.home() / '.garminconnect')
DB_PATH = 'dashboard.db'

# --- Databas ---
def db():
    return sqlite3.connect(DB_PATH)

def setup_db():
    with db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY, name TEXT, date TEXT, type TEXT,
            distance REAL, duration REAL, avg_hr INTEGER,
            raw TEXT, created_at REAL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY, value TEXT, updated_at REAL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS strength_exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            exercise TEXT NOT NULL,
            sets INTEGER,
            reps TEXT,
            weight REAL,
            note TEXT,
            created_at REAL)''')

setup_db()

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
        for a in activities:
            try:
                conn.execute(
                    'INSERT OR REPLACE INTO activities (id,name,date,type,distance,duration,avg_hr,raw,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                    (a.get('activityId'), a.get('activityName'), a.get('startTimeLocal'),
                     a.get('activityType', {}).get('typeKey'),
                     a.get('distance'), a.get('duration'), a.get('averageHR'),
                     json.dumps(a), time.time()))
            except Exception as e:
                print('Spara aktivitet fel:', e)

# --- Auth ---
@app.before_request
def check_auth():
    if not request.path.startswith('/api/'):
        return
    if request.path == '/api/login':
        return
    if request.headers.get('x-site-password') != PASSWORD:
        return jsonify({'error': 'Unauthorized'}), 401

# --- Endpoints ---
@app.post('/api/login')
def login():
    if request.json.get('password') == PASSWORD:
        return jsonify({'ok': True})
    return jsonify({'ok': False}), 401

@app.get('/api/status')
def status():
    return jsonify({'status': 'ok'})

@app.get('/api/activities')
def activities():
    with db() as conn:
        rows = conn.execute('SELECT raw FROM activities ORDER BY date DESC LIMIT 50').fetchall()
    if rows:
        return jsonify({'activities': [json.loads(r[0]) for r in rows], 'source': 'database'})
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

    with db() as conn:
        row = conn.execute("SELECT value, updated_at FROM cache WHERE key='health'").fetchone()
    if row and (time.time() - row[1]) < 30 * 60:
        return jsonify(json.loads(row[0]))

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
        light_sec = s.get('lightSleepSeconds', 0)
        sleep_scores = s.get('sleepScores') or {}
        sleep_score_val = sleep_scores.get('overall', {}).get('value') if isinstance(sleep_scores, dict) else None

        hrv_sum   = hrv.get('hrvSummary', {})
        hrv_pct   = round((hrv_sum.get('lastNightAvg', 0) / hrv_sum.get('weeklyAvg', 1)) * 100) if hrv_sum.get('weeklyAvg') else None

        bb_today  = bb[0] if bb else {}
        bb_vals   = bb_today.get('bodyBatteryValuesArray', [])
        bb_now    = bb_vals[-1][1] if bb_vals else None
        bb_max    = max(v[1] for v in bb_vals) if bb_vals else None

        ready     = readiness[0] if readiness else {}

        avg_resp  = resp.get('avgWakingRespirationValue') or resp.get('avgRespirationValue')
        sleep_resp = resp.get('avgSleepRespirationValue')

        avg_spo2 = spo2.get('averageSpO2')
        if avg_spo2: avg_spo2 = round(avg_spo2)
        min_spo2 = spo2.get('lowestSpO2')

        result = {
            'date': today,
            'readiness':     {'score': ready.get('score'), 'level': ready.get('level'), 'feedback': ready.get('feedbackShort')},
            'hrv':           {'lastNightAvg': hrv_sum.get('lastNightAvg'), 'weeklyAvg': hrv_sum.get('weeklyAvg'), 'status': hrv_sum.get('status'), 'pct': hrv_pct},
            'restingHR':     {'value': hr.get('restingHeartRate'), 'sevenDayAvg': hr.get('lastSevenDaysAvgRestingHeartRate'), 'min': hr.get('minHeartRate')},
            'sleep':         {'totalSec': total_sleep_sec, 'deepSec': deep_sec, 'remSec': rem_sec, 'lightSec': light_sec, 'score': sleep_score_val,
                              'deepPct': round(deep_sec/total_sleep_sec*100) if total_sleep_sec else 0,
                              'remPct':  round(rem_sec/total_sleep_sec*100)  if total_sleep_sec else 0},
            'bodyBattery':   {'current': bb_now, 'max': bb_max, 'charged': bb_today.get('charged'), 'drained': bb_today.get('drained')},
            'stress':        {'avg': stress.get('avgStressLevel'), 'max': stress.get('maxStressLevel')},
            'respiration':   {'avg': round(avg_resp) if avg_resp else None, 'sleepAvg': round(sleep_resp) if sleep_resp else None},
            'spo2':          {'avg': avg_spo2, 'min': min_spo2},
        }

        with db() as conn:
            conn.execute("INSERT OR REPLACE INTO cache (key,value,updated_at) VALUES ('health',?,?)",
                         (json.dumps(result), time.time()))
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.post('/api/sync')
def sync():
    try:
        client = get_garmin()
        acts = client.get_activities(0, 50)
        save_activities(acts)
        # Rensa cachad hälsodata så nästa anrop hämtar färsk data
        with db() as conn:
            conn.execute("DELETE FROM cache WHERE key IN ('health','analysis')")
        return jsonify({'ok': True, 'count': len(acts)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.post('/api/refresh')
def refresh():
    with db() as conn:
        row = conn.execute("SELECT value, updated_at FROM cache WHERE key='analysis'").fetchone()
    if row and (time.time() - row[1]) < 5 * 60:
        return jsonify(json.loads(row[0]))

    try:
        client = get_garmin()
        acts = client.get_activities(0, 10)
        save_activities(acts)
    except Exception as e:
        return jsonify({'error': 'Garmin-fel: ' + str(e)}), 500

    recent_runs = [
        {'name': a.get('activityName'), 'date': a.get('startTimeLocal'),
         'distance': f"{a.get('distance',0)/1000:.1f} km",
         'duration': f"{int(a.get('duration',0)/60)} min",
         'avgHR': a.get('averageHR'), 'trainingEffect': a.get('trainingEffectLabel')}
        for a in acts if 'running' in (a.get('activityType', {}).get('typeKey') or '')
    ][:5]

    if not ANTHROPIC_KEY or ANTHROPIC_KEY.startswith('sk-ant-placeholder'):
        return jsonify({'todayRecommendation': 'Lägg till Anthropic API-nyckel i .env för AI-analys.',
                        'todayType': 'easy',
                        'nextSession': {'title': 'Lugnt jogg', 'desc': 'Z2, 30-40 min', 'tempo': '4:45-5:15 /km', 'distance': '~6 km'},
                        'prediction3k': '10:27', 'insight': 'AI-insikter kräver API-nyckel.'})

    prompt = f"""Du är en träningscoach. Analysera och svara ENDAST med JSON.

Senaste löppass:
{json.dumps(recent_runs, ensure_ascii=False, indent=2)}

Mål: 3 km under 10 minuter. Bästa: 10:27.
Plan: återhämtning v.23 → intervaller v.24-25 → tröskel v.26-29 → spetsning v.30-34.

Svara ENDAST med detta JSON:
{{
  "todayRecommendation": "rekommendation idag (1-2 meningar)",
  "todayType": "easy|quality|rest",
  "nextSession": {{"title": "passnamn", "desc": "beskrivning", "tempo": "t.ex. 3:35 /km", "distance": "t.ex. ~8 km"}},
  "prediction3k": "t.ex. 10:15",
  "insight": "en konkret insikt (1 mening)"
}}"""

    resp = requests.post('https://api.anthropic.com/v1/messages',
        json={'model': 'claude-sonnet-4-6', 'max_tokens': 500,
              'messages': [{'role': 'user', 'content': prompt}]},
        headers={'x-api-key': ANTHROPIC_KEY, 'anthropic-version': '2023-06-01',
                 'content-type': 'application/json'})

    text = resp.json()['content'][0]['text'].strip().replace('```json', '').replace('```', '').strip()
    analysis = json.loads(text)

    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO cache (key,value,updated_at) VALUES ('analysis',?,?)",
                     (json.dumps(analysis), time.time()))

    return jsonify(analysis)

@app.post('/api/chat')
def chat():
    data = request.json
    if not ANTHROPIC_KEY:
        return jsonify({'reply': 'API-nyckel saknas.'})
    resp = requests.post('https://api.anthropic.com/v1/messages',
        json={'model': 'claude-sonnet-4-6', 'max_tokens': 1024,
              'system': data.get('context', 'Du är en personlig träningscoach. Svara på svenska.'),
              'messages': [{'role': 'user', 'content': data.get('message', '')}]},
        headers={'x-api-key': ANTHROPIC_KEY, 'anthropic-version': '2023-06-01',
                 'content-type': 'application/json'})
    return jsonify({'reply': resp.json()['content'][0]['text']})

# --- Styrka ---
STRENGTH_TYPES = {'strength_training', 'fitness_equipment', 'gym', 'indoor_cardio', 'cardio', 'bouldering'}

@app.get('/api/strength')
def strength_sessions():
    with db() as conn:
        rows = conn.execute("SELECT raw FROM activities WHERE type IN ({}) ORDER BY date DESC LIMIT 30".format(
            ','.join('?' * len(STRENGTH_TYPES))), list(STRENGTH_TYPES)).fetchall()
    sessions = []
    for r in rows:
        a = json.loads(r[0])
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
        rows = conn.execute(
            'SELECT id, exercise, sets, reps, weight, note FROM strength_exercises WHERE session_id=? ORDER BY id',
            (session_id,)).fetchall()
    return jsonify({'exercises': [{'id': r[0], 'exercise': r[1], 'sets': r[2], 'reps': r[3], 'weight': r[4], 'note': r[5]} for r in rows]})

@app.post('/api/strength/<session_id>/exercises')
def add_exercise(session_id):
    data = request.get_json(force=True, silent=True) or {}
    with db() as conn:
        cur = conn.execute(
            'INSERT INTO strength_exercises (session_id, exercise, sets, reps, weight, note, created_at) VALUES (?,?,?,?,?,?,?)',
            (session_id, data.get('exercise',''), data.get('sets'), data.get('reps',''), data.get('weight'), data.get('note',''), time.time()))
        new_id = cur.lastrowid
    return jsonify({'ok': True, 'id': new_id})

@app.delete('/api/strength/exercises/<int:ex_id>')
def delete_exercise(ex_id):
    with db() as conn:
        conn.execute('DELETE FROM strength_exercises WHERE id=?', (ex_id,))
    return jsonify({'ok': True})

# --- Statiska filer ---
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
