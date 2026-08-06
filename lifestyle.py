"""Lifestyle journal and cautious personal impact analysis for Trainyze."""

from __future__ import annotations

from datetime import date
import json
import math
import threading
import time


BEHAVIORS = (
    {'key': 'alcohol', 'label': 'Alkohol', 'good_when': False},
    {'key': 'late_caffeine', 'label': 'Koffein efter 14', 'good_when': False},
    {'key': 'late_meal', 'label': 'Sen måltid', 'good_when': False},
    {'key': 'hydration', 'label': 'Minst 2 liter vätska', 'good_when': True},
    {'key': 'protein_target', 'label': 'Proteinmål uppnått', 'good_when': True},
    {'key': 'screen_before_bed', 'label': 'Skärm sista timmen', 'good_when': False},
    {'key': 'meditation', 'label': 'Minst 10 min nedvarvning', 'good_when': True},
    {'key': 'outdoors', 'label': 'Minst 20 min utomhus', 'good_when': True},
)


def _bounded_number(data, key, low, high, integer=False):
    value = data.get(key)
    if value in (None, ''):
        return None
    try:
        value = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{key} måste vara ett tal.') from exc
    if not low <= value <= high:
        raise ValueError(f'{key} måste vara {low}–{high}.')
    return value


def _optional_bool(data, key):
    value = data.get(key)
    if value in (None, ''):
        return None
    if value is True or str(value).lower() in ('1', 'true', 'yes', 'on'):
        return True
    if value is False or str(value).lower() in ('0', 'false', 'no', 'off'):
        return False
    raise ValueError(f'{key} måste vara ja eller nej.')


def _time(data, key):
    value = str(data.get(key) or '').strip()
    if not value:
        return None
    pieces = value.split(':')
    if len(pieces) != 2:
        raise ValueError(f'{key} måste vara en giltig tid.')
    try:
        hour, minute = int(pieces[0]), int(pieces[1])
    except ValueError as exc:
        raise ValueError(f'{key} måste vara en giltig tid.') from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f'{key} måste vara en giltig tid.')
    return f'{hour:02d}:{minute:02d}'


def normalize_entry(data):
    return {
        'nutrition_quality': _bounded_number(data, 'nutrition_quality', 1, 5, True),
        'fruit_veg_servings': _bounded_number(data, 'fruit_veg_servings', 0, 20, True),
        'protein_target': _optional_bool(data, 'protein_target'),
        'late_meal': _optional_bool(data, 'late_meal'),
        'alcohol_drinks': _bounded_number(data, 'alcohol_drinks', 0, 30, True),
        'alcohol_last_time': _time(data, 'alcohol_last_time'),
        'caffeine_servings': _bounded_number(data, 'caffeine_servings', 0, 20, True),
        'caffeine_last_time': _time(data, 'caffeine_last_time'),
        'water_liters': _bounded_number(data, 'water_liters', 0, 15),
        'outdoor_minutes': _bounded_number(data, 'outdoor_minutes', 0, 1440, True),
        'meditation_minutes': _bounded_number(data, 'meditation_minutes', 0, 600, True),
        'screen_before_bed': _optional_bool(data, 'screen_before_bed'),
        'travel': _optional_bool(data, 'travel'),
        'medication_change': _optional_bool(data, 'medication_change'),
        'note': str(data.get('note') or '').strip()[:1000],
    }


def behavior_value(key, entry):
    """Return True/False only when the user supplied enough information."""
    if key == 'alcohol':
        value = entry.get('alcohol_drinks')
        return None if value is None else value > 0
    if key == 'late_caffeine':
        servings = entry.get('caffeine_servings')
        last_time = entry.get('caffeine_last_time')
        if servings is None:
            return None
        if servings == 0:
            return False
        return None if not last_time else last_time >= '14:00'
    if key == 'late_meal':
        return entry.get('late_meal')
    if key == 'hydration':
        value = entry.get('water_liters')
        return None if value is None else value >= 2
    if key == 'protein_target':
        return entry.get('protein_target')
    if key == 'screen_before_bed':
        return entry.get('screen_before_bed')
    if key == 'meditation':
        value = entry.get('meditation_minutes')
        return None if value is None else value >= 10
    if key == 'outdoors':
        value = entry.get('outdoor_minutes')
        return None if value is None else value >= 20
    return None


def _mean(values):
    values = [float(value) for value in values if value is not None]
    return sum(values) / len(values) if values else None


def _std(values, mean):
    values = [float(value) for value in values if value is not None]
    if len(values) < 2 or mean is None:
        return None
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)) or None


def analyze_impacts(rows):
    """Associate a day's behavior with the following day's wearable outcomes.

    This is deliberately descriptive, not causal. A minimum of five exposed and
    five unexposed days mirrors WHOOP's guardrail against instant conclusions.
    """
    metrics = ('sleep_score', 'hrv', 'resting_hr')
    baselines = {}
    for metric in metrics:
        values = [(row.get('outcome') or {}).get(metric) for row in rows]
        mean = _mean(values)
        baselines[metric] = (mean, _std(values, mean))

    scored = []
    for row in rows:
        outcome = row.get('outcome') or {}
        z_values = []
        for metric in metrics:
            value = outcome.get(metric)
            mean, std = baselines[metric]
            if value is None or mean is None or not std:
                continue
            z = (float(value) - mean) / std
            z_values.append(-z if metric == 'resting_hr' else z)
        scored.append({**row, 'recovery_score': 50 + 10 * _mean(z_values) if z_values else None})

    insights = []
    for definition in BEHAVIORS:
        yes, no = [], []
        yes_rows, no_rows = [], []
        for row in scored:
            state = behavior_value(definition['key'], row.get('data') or {})
            score = row.get('recovery_score')
            if state is True and score is not None:
                yes.append(score); yes_rows.append(row)
            elif state is False and score is not None:
                no.append(score); no_rows.append(row)
        ready = len(yes) >= 5 and len(no) >= 5
        delta = round(_mean(yes) - _mean(no), 1) if ready else None
        components = {}
        if ready:
            for metric in metrics:
                yes_mean = _mean([(row.get('outcome') or {}).get(metric) for row in yes_rows])
                no_mean = _mean([(row.get('outcome') or {}).get(metric) for row in no_rows])
                components[metric] = round(yes_mean - no_mean, 1) if yes_mean is not None and no_mean is not None else None
        insights.append({
            **definition, 'yesDays': len(yes), 'noDays': len(no), 'ready': ready,
            'impact': delta, 'components': components,
            'confidence': 'medium' if ready and min(len(yes), len(no)) >= 10 else ('early' if ready else 'collecting'),
        })
    return {'windowDays': 90, 'minimumPerGroup': 5, 'daysWithOutcomes': sum(row['recovery_score'] is not None for row in scored),
            'insights': insights, 'causal': False}


class LifestyleStore:
    def __init__(self, db_factory=None):
        self._db = db_factory
        self._lock = threading.Lock()
        self._entries = {}

    def ensure_schema(self):
        if not self._db:
            return
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''CREATE TABLE IF NOT EXISTS lifestyle_logs (
                    user_id INTEGER NOT NULL, log_date DATE NOT NULL, data JSONB NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, log_date))''')
                cur.execute('''CREATE INDEX IF NOT EXISTS lifestyle_logs_recent_idx
                    ON lifestyle_logs (user_id, log_date DESC)''')
            conn.commit()

    def save(self, user_id, log_date, data):
        value = normalize_entry(data)
        key = (user_id, str(log_date))
        if not self._db:
            with self._lock:
                self._entries[key] = value
            return dict(value)
        now = time.time()
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''INSERT INTO lifestyle_logs (user_id,log_date,data,created_at,updated_at)
                    VALUES (%s,%s,%s::jsonb,%s,%s)
                    ON CONFLICT (user_id,log_date) DO UPDATE SET
                    data=EXCLUDED.data,updated_at=EXCLUDED.updated_at''',
                    (user_id, str(log_date), json.dumps(value, ensure_ascii=False), now, now))
            conn.commit()
        return value

    def get(self, user_id, log_date):
        if not self._db:
            with self._lock:
                value = self._entries.get((user_id, str(log_date)))
                return dict(value) if value else {}
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT data FROM lifestyle_logs WHERE user_id=%s AND log_date=%s',
                            (user_id, str(log_date)))
                row = cur.fetchone()
        return dict(row[0]) if row else {}

    def rows_with_outcomes(self, user_id, since):
        if not self._db:
            return []
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT l.log_date,l.data,h.sleep_score,h.hrv_avg,h.resting_hr
                    FROM lifestyle_logs l
                    LEFT JOIN health_history h ON h.user_id=l.user_id AND h.date=l.log_date + 1
                    WHERE l.user_id=%s AND l.log_date >= %s ORDER BY l.log_date''',
                    (user_id, str(since)))
                rows = cur.fetchall()
        return [{'date': str(row[0]), 'data': dict(row[1]),
                 'outcome': {'sleep_score': row[2], 'hrv': row[3], 'resting_hr': row[4]}}
                for row in rows]
