"""Validate the complete coach proposal before opening a write transaction."""
import math
from datetime import date


SESSION_TYPES = ('run', 'easy', 'lift', 'race', 'rest')
_FIELDS = {
    'session_id': {'type': ['integer', 'null']},
    'action': {'type': 'string', 'enum': ['add', 'skip', 'reschedule', 'modify', 'keep']},
    'new_week': {'type': ['integer', 'null']},
    'new_dow': {'type': ['integer', 'null']},
    # Anthropic rejects enum values alongside a nullable type array. Keep
    # the enum on the string branch and allow null through a separate branch.
    'type': {'anyOf': [
        {'type': 'string', 'enum': list(SESSION_TYPES)},
        {'type': 'null'},
    ]},
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
    """Förslaget avvisades i sin helhet.

    `reason` är den regel som brast. Användaren ska bara se `message` — men utan
    regeln i loggen blir ett avvisat förslag ett 502 utan spår, och då går det
    inte att se om det var modellen eller valideringen som hade fel."""

    def __init__(self, message, reason=None):
        super().__init__(message)
        self.reason = reason or message


def validate_proposal(result, sessions, today):
    """Reject the entire proposal on invalid data; never silently apply half."""
    def fail(reason):
        raise InvalidPlanChange('AI-svaret innehöll en ogiltig planändring. Inga pass ändrades.',
                                reason=reason)

    if not isinstance(result, dict) or not isinstance(result.get('changes'), list):
        fail('changes saknas eller är inte en lista')
    for key in ('summary', 'coaching_notes'):
        if not isinstance(result.get(key, ''), str):
            fail(f'{key} är inte en sträng')
    if len(result['changes']) > 60:
        fail(f"för många ändringar ({len(result['changes'])})")
    known = {s['id']: s for s in sessions}
    seen = set()
    for change in result['changes']:
        if not isinstance(change, dict):
            fail('en ändring är inte ett objekt')
        action = change.get('action')
        if action not in ('add', 'skip', 'reschedule', 'modify', 'keep'):
            fail(f'okänd action {action!r}')
        sid = change.get('session_id')
        if action == 'add':
            if sid is not None:
                fail('add har ett session_id')
        else:
            if type(sid) is not int or sid not in known or sid in seen:
                fail(f'{action} pekar på okänt eller upprepat session_id {sid!r}')
            seen.add(sid)
        for key in ('new_title', 'new_detail', 'reason'):
            value = change.get(key)
            if value is not None and (not isinstance(value, str) or len(value) > 4000):
                fail(f'{key} är inte en rimlig sträng')
            if key != 'reason' and value is not None and not value.strip():
                fail(f'{key} är tom')
        kind = change.get('type')
        if kind is not None and kind not in SESSION_TYPES:
            fail(f'okänd passtyp {kind!r}')
        km = change.get('new_km')
        if km is not None and (type(km) not in (int, float) or not math.isfinite(km) or km < 0):
            fail(f'ogiltig new_km {km!r}')
        # keep och skip flyttar ingenting, så ett datum på dem betyder inget och
        # används inte när ändringen skrivs. Modellen fyller ändå ofta i passets
        # nuvarande dag — och för ett missat pass ligger den i det förflutna.
        # Att avvisa hela förslaget för ett fält som ignoreras nedströms gjorde
        # att en fullt rimlig omplanering blev ett 502 i chatten.
        if action in ('keep', 'skip'):
            change['new_week'] = None
            change['new_dow'] = None
        week, dow = change.get('new_week'), change.get('new_dow')
        if action in ('add', 'reschedule') or week is not None or dow is not None:
            if type(week) is not int or type(dow) is not int or not 0 <= dow <= 6:
                fail(f'{action} saknar giltig vecka/dag ({week!r}, {dow!r})')
            try:
                target = date.fromisocalendar(today.isocalendar().year, week, dow + 1)
            except ValueError:
                fail(f'vecka {week} dag {dow} är inget riktigt datum')
            if target < today:
                fail(f'{action} lägger passet på {target}, som redan passerat')
        if action == 'add' and not all(
                isinstance(change.get(k), str) and change[k].strip()
                for k in ('new_title', 'new_detail')):
            fail('add saknar titel eller innehåll')
        if action == 'modify':
            if known[sid]['status'] != 'planned':
                fail(f"modify träffar ett pass med status {known[sid]['status']!r}")
            if not any(change.get(k) is not None for k in (
                    'new_title', 'new_detail', 'new_km', 'type', 'new_week')):
                fail('modify ändrar ingenting')
    return result
