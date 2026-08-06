"""Subjective post-workout feedback, stored alongside wearable activity data."""

from __future__ import annotations

import json
import threading
import time


MEAL_VALUES = {'none', 'light', 'normal', 'heavy'}
HYDRATION_VALUES = {'low', 'okay', 'good'}


def normalize_feedback(data):
    def bounded(name, low, high):
        value = data.get(name)
        if value in (None, ''):
            return None
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'{name} måste vara ett tal.') from exc
        if not low <= value <= high:
            raise ValueError(f'{name} måste vara {low}–{high}.')
        return value

    meal = str(data.get('meal_before') or '').strip().lower() or None
    hydration = str(data.get('hydration') or '').strip().lower() or None
    if meal not in MEAL_VALUES | {None}:
        raise ValueError('Ogiltigt val för mat före passet.')
    if hydration not in HYDRATION_VALUES | {None}:
        raise ValueError('Ogiltigt val för vätska.')
    return {
        'feeling': bounded('feeling', 1, 5),
        'effort': bounded('effort', 1, 10),
        'meal_before': meal,
        'hydration': hydration,
        'notes': str(data.get('notes') or '').strip()[:1500],
    }


class ActivityFeedbackStore:
    def __init__(self, db_factory=None):
        self._db = db_factory
        self._lock = threading.Lock()
        self._values = {}

    def ensure_schema(self):
        if not self._db:
            return
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''CREATE TABLE IF NOT EXISTS activity_feedback (
                    user_id INTEGER NOT NULL, source TEXT NOT NULL,
                    activity_id BIGINT NOT NULL, data JSONB NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, source, activity_id))''')
                cur.execute('''CREATE INDEX IF NOT EXISTS activity_feedback_recent_idx
                    ON activity_feedback (user_id, updated_at DESC)''')
            conn.commit()

    def get(self, user_id, source, activity_id):
        key = (int(user_id), str(source), int(activity_id))
        if not self._db:
            with self._lock:
                value = self._values.get(key)
                return dict(value) if value else {}
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT data FROM activity_feedback
                    WHERE user_id=%s AND source=%s AND activity_id=%s''', key)
                row = cur.fetchone()
        return dict(row[0]) if row else {}

    def save(self, user_id, source, activity_id, data):
        value = normalize_feedback(data)
        key = (int(user_id), str(source), int(activity_id))
        if not self._db:
            with self._lock:
                self._values[key] = value
            return dict(value)
        now = time.time()
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute('''INSERT INTO activity_feedback
                    (user_id,source,activity_id,data,created_at,updated_at)
                    VALUES (%s,%s,%s,%s::jsonb,%s,%s)
                    ON CONFLICT (user_id,source,activity_id) DO UPDATE SET
                    data=EXCLUDED.data,updated_at=EXCLUDED.updated_at''',
                    (*key, json.dumps(value, ensure_ascii=False), now, now))
            conn.commit()
        return value
