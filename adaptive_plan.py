"""Deterministic, explainable daily adaptation for Trainyze plans.

The engine deliberately does not write to a plan. It turns a structured snapshot
into a bounded proposal. Persistence and eventual approval live outside this
module, which keeps the safety rules replayable against historical data.
"""

from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
import threading
import time
import uuid


SCHEMA_VERSION = 1
QUALITY_KINDS = {'interval', 'threshold', 'race', 'quality'}


def _number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _signal(key, label, value, state, weight, reason):
    return {'key': key, 'label': label, 'value': value, 'state': state,
            'weight': weight, 'reason': reason}


def _health_signals(health, checkin):
    signals = []
    fresh_sleep = not health.get('sleep_stale')
    sleep = _number(health.get('sleep_hours')) if fresh_sleep else None
    if sleep is not None:
        if sleep < 5.5:
            signals.append(_signal('sleep', 'Sömn', sleep, 'strong_negative', -2,
                                   f'Bara {sleep:.1f} timmars färsk sömn.'))
        elif sleep < 6.5:
            signals.append(_signal('sleep', 'Sömn', sleep, 'negative', -1,
                                   f'Kort natt: {sleep:.1f} timmar.'))
        elif sleep >= 7.5:
            signals.append(_signal('sleep', 'Sömn', sleep, 'positive', 1,
                                   f'{sleep:.1f} timmars sömn stödjer normal träning.'))

    readiness = _number(health.get('readiness'))
    if readiness is not None:
        if readiness < 35:
            signals.append(_signal('readiness', 'Träningsberedskap', readiness,
                                   'strong_negative', -2, f'Låg beredskap: {readiness:.0f}/100.'))
        elif readiness < 50:
            signals.append(_signal('readiness', 'Träningsberedskap', readiness,
                                   'negative', -1, f'Beredskap under din norm: {readiness:.0f}/100.'))
        elif readiness >= 70:
            signals.append(_signal('readiness', 'Träningsberedskap', readiness,
                                   'positive', 1, f'God beredskap: {readiness:.0f}/100.'))

    hrv = _number(health.get('hrv'))
    hrv_base = _number(health.get('hrv_baseline'))
    if hrv is not None and hrv_base and hrv_base > 0:
        delta = (hrv - hrv_base) / hrv_base * 100
        if delta <= -25:
            signals.append(_signal('hrv', 'HRV mot baslinje', round(delta, 1),
                                   'strong_negative', -2, f'HRV är {abs(delta):.0f} % under baslinjen.'))
        elif delta <= -15:
            signals.append(_signal('hrv', 'HRV mot baslinje', round(delta, 1),
                                   'negative', -1, f'HRV är {abs(delta):.0f} % under baslinjen.'))
        elif delta >= 8:
            signals.append(_signal('hrv', 'HRV mot baslinje', round(delta, 1),
                                   'positive', 1, f'HRV är {delta:.0f} % över baslinjen.'))

    rhr = _number(health.get('resting_hr'))
    rhr_base = _number(health.get('resting_hr_baseline'))
    if rhr is not None and rhr_base is not None:
        delta = rhr - rhr_base
        if delta >= 12:
            signals.append(_signal('resting_hr', 'Vilopuls mot baslinje', round(delta, 1),
                                   'strong_negative', -2, f'Vilopulsen är {delta:.0f} slag över baslinjen.'))
        elif delta >= 7:
            signals.append(_signal('resting_hr', 'Vilopuls mot baslinje', round(delta, 1),
                                   'negative', -1, f'Vilopulsen är {delta:.0f} slag över baslinjen.'))

    energy = _number(checkin.get('energy'))
    if energy is not None:
        if energy <= 2:
            signals.append(_signal('energy', 'Egen energi', energy, 'strong_negative', -2,
                                   f'Energin är bara {energy:.0f}/10.'))
        elif energy <= 4:
            signals.append(_signal('energy', 'Egen energi', energy, 'negative', -1,
                                   f'Låg upplevd energi: {energy:.0f}/10.'))
        elif energy >= 8:
            signals.append(_signal('energy', 'Egen energi', energy, 'positive', 1,
                                   f'God upplevd energi: {energy:.0f}/10.'))

    soreness = _number(checkin.get('soreness'))
    if soreness is not None and soreness >= 7:
        weight = -2 if soreness >= 9 else -1
        state = 'strong_negative' if soreness >= 9 else 'negative'
        signals.append(_signal('soreness', 'Muskelömhet', soreness, state, weight,
                               f'Muskelömheten är {soreness:.0f}/10.'))

    pain = _number(checkin.get('pain'))
    if pain is not None and pain >= 4:
        weight = -2 if pain >= 7 else -1
        state = 'strong_negative' if pain >= 7 else 'negative'
        area = str(checkin.get('pain_area') or '').strip()
        signals.append(_signal('pain', 'Smärta', pain, state, weight,
                               f'Smärta {pain:.0f}/10' + (f' i {area}' if area else '') + '.'))

    stress = _number(checkin.get('stress'))
    if stress is not None and stress >= 8:
        signals.append(_signal('subjective_stress', 'Upplevd stress', stress, 'negative', -1,
                               f'Hög upplevd stress: {stress:.0f}/10.'))
    return signals


def _data_quality(health, checkin):
    present = 0
    possible = 6
    for value in (health.get('sleep_hours') if not health.get('sleep_stale') else None,
                  health.get('readiness'), health.get('hrv'), health.get('resting_hr'),
                  checkin.get('energy'), checkin.get('pain')):
        present += value is not None
    ratio = present / possible
    return {'level': 'high' if ratio >= .75 else ('medium' if ratio >= .4 else 'low'),
            'availableSignals': present, 'possibleSignals': possible,
            'sleepFresh': not health.get('sleep_stale')}


def evaluate(snapshot, today=None):
    """Return an explainable, bounded proposal without mutating the plan."""
    today = today or date.today()
    session = snapshot.get('session') or None
    health = snapshot.get('health') or {}
    checkin = snapshot.get('checkin') or {}
    load = snapshot.get('load') or {}
    signals = _health_signals(health, checkin)
    hard_days = int(_number(load.get('hard_days_last_3')) or 0)
    if hard_days >= 2:
        signals.append(_signal('hard_days', 'Nylig intensitet', hard_days, 'negative', -1,
                               'Minst två belastande dagar under de senaste tre dygnen.'))

    illness = checkin.get('illness') is True
    symptoms = str(checkin.get('illness_symptoms') or '').strip()
    pain = _number(checkin.get('pain')) or 0
    quality = _data_quality(health, checkin)
    strong = [s for s in signals if s['state'] == 'strong_negative']
    negatives = [s for s in signals if s['weight'] < 0]
    score = sum(s['weight'] for s in signals)
    reasons = [s['reason'] for s in sorted(signals, key=lambda item: item['weight'])[:4]]
    warnings = []

    if illness:
        action = 'rest'
        headline = 'Vila och följ sjukdomssymtomen'
        detail = 'Sjukdom väger tyngre än klockdata. Planen bör pausas i dag och bedömas igen i morgon.'
        reasons.insert(0, 'Sjukdom rapporterad' + (f': {symptoms}' if symptoms else '.'))
    elif pain >= 7:
        action = 'rest'
        headline = 'Avstå dagens träningspass'
        detail = 'Hög smärta ska inte tränas igenom. Planen bör pausas tills belastning kan ske utan tydlig försämring.'
        warnings.append('Sök vård vid akut skada, bröstsmärta, andningsbesvär eller snabbt ökande symtom.')
    elif not session:
        action = 'no_session'
        headline = 'Ingen planerad träning i dag'
        detail = 'Behåll återhämtningen. Motorn ändrar inte vilodagar utan ett tydligt skäl.'
    else:
        kind = str(session.get('kind') or session.get('type') or '').lower()
        is_quality = kind in QUALITY_KINDS or bool(session.get('is_quality'))
        if is_quality and (strong or len(negatives) >= 3):
            action = 'reschedule'
            headline = f'Flytta {session.get("title") or "kvalitetspasset"}'
            detail = 'Flera oberoende signaler pekar nedåt. Flytta kvaliteten ett dygn och håll dagens aktivitet lätt.'
        elif (is_quality and len(negatives) >= 2) or (not is_quality and strong):
            action = 'reduce'
            headline = f'Gör {session.get("title") or "passet"} lättare'
            detail = 'Behåll vanan men minska dosen. Undvik att jaga planerade farter när kroppen svarar sämre.'
        else:
            action = 'keep'
            headline = f'Kör {session.get("title") or "dagens pass"} enligt plan'
            detail = ('Ingen enskild svag signal får ensam välta planen. '
                      'Det samlade underlaget stödjer det planerade passet.')

        available = _number(checkin.get('available_minutes'))
        estimated = _number(session.get('estimated_minutes'))
        if action == 'keep' and available is not None and estimated and 0 < available < estimated:
            action = 'reduce'
            headline = f'Korta {session.get("title") or "passet"} till tiden du har'
            detail = ('Behåll passets syfte men anpassa dosen till din tillgängliga tid. '
                      'Kontinuitet är bättre än att behöva hoppa över hela passet.')
            reasons.insert(0, f'Du har {available:.0f} minuter och passet uppskattas till {estimated:.0f} minuter.')

    proposed = None
    if session and action in ('reduce', 'reschedule', 'rest'):
        proposed = {'sessionId': session.get('id'), 'action': action}
        if action == 'reduce':
            km = _number(session.get('km'))
            proposed.update({'distanceFactor': .7,
                             'km': round(km * .7, 1) if km else None,
                             'intensity': 'easy'})
        elif action == 'reschedule':
            proposed.update({'delayDays': 1,
                             'suggestedDate': (today + timedelta(days=1)).isoformat()})

    if not reasons:
        reasons = ['Inga tydliga negativa avvikelser hittades i det tillgängliga underlaget.']
    return {
        'schemaVersion': SCHEMA_VERSION, 'date': today.isoformat(), 'mode': 'shadow',
        'action': action, 'headline': headline, 'detail': detail,
        'score': score, 'confidence': quality['level'], 'dataQuality': quality,
        'reasons': reasons, 'warnings': warnings, 'signals': signals,
        'session': session, 'proposedChange': proposed,
    }


def snapshot_hash(snapshot):
    payload = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


class AdaptivePlanStore:
    def __init__(self, db_factory=None):
        self._db = db_factory
        self._lock = threading.Lock()
        self._checkins = {}
        self._decisions = {}

    def ensure_schema(self):
        if not self._db:
            return
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''CREATE TABLE IF NOT EXISTS adaptive_plan_checkins (
                    user_id INTEGER NOT NULL, checkin_date DATE NOT NULL,
                    energy INTEGER, soreness INTEGER, pain INTEGER, pain_area TEXT,
                    illness BOOLEAN NOT NULL DEFAULT FALSE, illness_symptoms TEXT,
                    stress INTEGER, available_minutes INTEGER, note TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, checkin_date))''')
                cur.execute('''CREATE TABLE IF NOT EXISTS adaptive_plan_decisions (
                    id UUID PRIMARY KEY, user_id INTEGER NOT NULL, decision_date DATE NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'shadow', status TEXT NOT NULL DEFAULT 'shadow',
                    input_hash TEXT NOT NULL, snapshot JSONB NOT NULL, decision JSONB NOT NULL,
                    created_at REAL NOT NULL, decided_at REAL)''')
                cur.execute('''CREATE INDEX IF NOT EXISTS adaptive_decisions_recent_idx
                    ON adaptive_plan_decisions (user_id, decision_date DESC, created_at DESC)''')
                cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS adaptive_decisions_input_idx
                    ON adaptive_plan_decisions (user_id, decision_date, input_hash)''')
            conn.commit()

    @staticmethod
    def normalize_checkin(data):
        def bounded(name, low, high):
            value = data.get(name)
            if value in (None, ''):
                return None
            value = int(value)
            if not low <= value <= high:
                raise ValueError(f'{name} måste vara {low}–{high}.')
            return value
        return {
            'energy': bounded('energy', 1, 10), 'soreness': bounded('soreness', 0, 10),
            'pain': bounded('pain', 0, 10), 'pain_area': str(data.get('pain_area') or '').strip()[:100],
            'illness': data.get('illness') is True or str(data.get('illness') or '').lower() in ('1', 'true', 'on'),
            'illness_symptoms': str(data.get('illness_symptoms') or '').strip()[:300],
            'stress': bounded('stress', 1, 10),
            'available_minutes': bounded('available_minutes', 0, 600),
            'note': str(data.get('note') or '').strip()[:1000],
        }

    def save_checkin(self, user_id, checkin_date, data):
        value = self.normalize_checkin(data)
        value.update(user_id=user_id, checkin_date=str(checkin_date))
        now = time.time()
        if not self._db:
            with self._lock:
                self._checkins[(user_id, str(checkin_date))] = dict(value)
            return value
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''INSERT INTO adaptive_plan_checkins
                    (user_id,checkin_date,energy,soreness,pain,pain_area,illness,
                     illness_symptoms,stress,available_minutes,note,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (user_id,checkin_date) DO UPDATE SET
                    energy=EXCLUDED.energy,soreness=EXCLUDED.soreness,pain=EXCLUDED.pain,
                    pain_area=EXCLUDED.pain_area,illness=EXCLUDED.illness,
                    illness_symptoms=EXCLUDED.illness_symptoms,stress=EXCLUDED.stress,
                    available_minutes=EXCLUDED.available_minutes,note=EXCLUDED.note,
                    updated_at=EXCLUDED.updated_at''',
                    (user_id, str(checkin_date), value['energy'], value['soreness'], value['pain'],
                     value['pain_area'], value['illness'], value['illness_symptoms'], value['stress'],
                     value['available_minutes'], value['note'], now, now))
            conn.commit()
        return value

    def get_checkin(self, user_id, checkin_date):
        if not self._db:
            with self._lock:
                value = self._checkins.get((user_id, str(checkin_date)))
                return dict(value) if value else {}
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT energy,soreness,pain,pain_area,illness,illness_symptoms,
                                      stress,available_minutes,note FROM adaptive_plan_checkins
                               WHERE user_id=%s AND checkin_date=%s''', (user_id, str(checkin_date)))
                row = cur.fetchone()
        if not row:
            return {}
        keys = ('energy','soreness','pain','pain_area','illness','illness_symptoms',
                'stress','available_minutes','note')
        return dict(zip(keys, row))

    def save_decision(self, user_id, decision_date, snapshot, decision):
        digest = snapshot_hash(snapshot)
        now = time.time()
        if not self._db:
            key = (user_id, str(decision_date), digest)
            with self._lock:
                value = self._decisions.get(key)
                if not value:
                    value = {'id': str(uuid.uuid4()), 'input_hash': digest, 'created_at': now,
                             'snapshot': snapshot, 'decision': decision, 'status': 'shadow'}
                    self._decisions[key] = value
                return dict(value)
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''INSERT INTO adaptive_plan_decisions
                    (id,user_id,decision_date,mode,status,input_hash,snapshot,decision,created_at)
                    VALUES (%s,%s,%s,'shadow','shadow',%s,%s::jsonb,%s::jsonb,%s)
                    ON CONFLICT (user_id,decision_date,input_hash) DO UPDATE
                    SET decision=EXCLUDED.decision RETURNING id,input_hash,created_at,status''',
                    (str(uuid.uuid4()), user_id, str(decision_date), digest,
                     json.dumps(snapshot, ensure_ascii=False, default=str),
                     json.dumps(decision, ensure_ascii=False, default=str), now))
                row = cur.fetchone()
            conn.commit()
        return {'id': str(row[0]), 'input_hash': row[1], 'created_at': row[2],
                'status': row[3], 'snapshot': snapshot, 'decision': decision}
