"""Validate the complete coach proposal before opening a write transaction."""
import math
from datetime import date


SESSION_TYPES = ('run', 'easy', 'lift', 'race', 'rest')
_FIELDS = {
    'session_id': {'type': ['integer', 'null']},
    'action': {'type': 'string', 'enum': ['add', 'skip', 'reschedule', 'modify', 'keep']},
    'new_week': {'type': ['integer', 'null']},
    'new_dow': {'type': ['integer', 'null']},
    'type': {'type': ['string', 'null'], 'enum': [*SESSION_TYPES, None]},
    'new_km': {'type': ['number', 'null']},
    'new_title': {'type': ['string', 'null']},
    'new_detail': {'type': ['string', 'null']},
    'reason': {'type': 'string'},
}
PLAN_CHANGE_SCHEMA = {
    'type': 'object',
    'properties': {
        'changes': {'type': 'array', 'items': {
            'type': 'object', 'properties': _FIELDS,
            'required': list(_FIELDS), 'additionalProperties': False,
        }},
        'summary': {'type': 'string'},
        'coaching_notes': {'type': 'string'},
    },
    'required': ['changes', 'summary', 'coaching_notes'],
    'additionalProperties': False,
}


class InvalidPlanChange(ValueError):
    pass


def validate_proposal(result, sessions, today):
    """Reject the entire proposal on invalid data; never silently apply half."""
    def fail():
        raise InvalidPlanChange('AI-svaret innehöll en ogiltig planändring. Inga pass ändrades.')

    if not isinstance(result, dict) or not isinstance(result.get('changes'), list):
        fail()
    for key in ('summary', 'coaching_notes'):
        if not isinstance(result.get(key, ''), str):
            fail()
    if len(result['changes']) > 60:
        fail()
    known = {s['id']: s for s in sessions}
    seen = set()
    for change in result['changes']:
        if not isinstance(change, dict):
            fail()
        action = change.get('action')
        if action not in ('add', 'skip', 'reschedule', 'modify', 'keep'):
            fail()
        sid = change.get('session_id')
        if action == 'add':
            if sid is not None:
                fail()
        else:
            if type(sid) is not int or sid not in known or sid in seen:
                fail()
            seen.add(sid)
        for key in ('new_title', 'new_detail', 'reason'):
            value = change.get(key)
            if value is not None and (not isinstance(value, str) or len(value) > 4000):
                fail()
            if key != 'reason' and value is not None and not value.strip():
                fail()
        kind = change.get('type')
        if kind is not None and kind not in SESSION_TYPES:
            fail()
        km = change.get('new_km')
        if km is not None and (type(km) not in (int, float) or not math.isfinite(km) or km < 0):
            fail()
        week, dow = change.get('new_week'), change.get('new_dow')
        if action in ('add', 'reschedule') or week is not None or dow is not None:
            if type(week) is not int or type(dow) is not int or not 0 <= dow <= 6:
                fail()
            try:
                target = date.fromisocalendar(today.isocalendar().year, week, dow + 1)
            except ValueError:
                fail()
            if target < today:
                fail()
        if action == 'add' and not all(
                isinstance(change.get(k), str) and change[k].strip()
                for k in ('new_title', 'new_detail')):
            fail()
        if action == 'modify':
            if known[sid]['status'] != 'planned':
                fail()
            if not any(change.get(k) is not None for k in (
                    'new_title', 'new_detail', 'new_km', 'type', 'new_week')):
                fail()
    return result
