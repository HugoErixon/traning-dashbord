"""Niggles: a quick log of aches ("vänster trampdyna 3/10") and what follows.

The watch cannot see a sore foot, and a coach that does not know about one will
happily prescribe hill sprints. Each log is one reading for a body area and
side; the latest reading per area decides whether it is active. A reading of 0
closes it. An active niggle that has not been updated for a few days asks for a
check-in, so a forgotten entry does not keep shaping the plan for weeks — and
one that quietly got worse does not go unnoticed either.
"""

from datetime import date

AREAS = {
    'foot': 'Fot / trampdyna', 'achilles': 'Hälsena', 'calf': 'Vad', 'shin': 'Benhinna',
    'knee': 'Knä', 'hip': 'Höft / ljumske', 'hamstring': 'Baksida lår', 'quad': 'Framsida lår',
    'back': 'Rygg', 'shoulder': 'Axel', 'other': 'Annat',
}
SIDES = {'left': 'vänster', 'right': 'höger', 'both': 'båda', '': ''}
LOWER_BODY = {'foot', 'achilles', 'calf', 'shin', 'knee', 'hip', 'hamstring', 'quad'}
ACTIVE_DAYS = 28        # en känning som inte uppdaterats på fyra veckor räknas som borta
CHECK_IN_AFTER_DAYS = 3
MAX_ENTRIES = 200


def normalize(data, today):
    area = str(data.get('area') or '').strip()
    if area not in AREAS:
        raise ValueError('Välj var det känns.')
    side = str(data.get('side') or '').strip()
    if side not in SIDES:
        raise ValueError('Ogiltig sida.')
    try:
        severity = int(data.get('severity'))
    except (TypeError, ValueError) as exc:
        raise ValueError('Ange hur ont det gör, 0–10.') from exc
    if not 0 <= severity <= 10:
        raise ValueError('Ange hur ont det gör, 0–10.')
    return {'area': area, 'side': side, 'severity': severity,
            'note': str(data.get('note') or '').strip()[:200], 'date': today.isoformat()}


def label(area, side):
    side_text = SIDES.get(side or '', '')
    name = AREAS.get(area, area)
    return f'{name} ({side_text})' if side_text else name


def add(entries, entry, now_ts):
    entry = dict(entry, ts=now_ts)
    return (list(entries or []) + [entry])[-MAX_ENTRIES:]


def advice(severity, area):
    lower = area in LOWER_BODY
    if severity >= 7:
        return ('Ingen löpning. Byt till cykel eller vila och kontakta en fysioterapeut om det inte släpper på ett par dagar.'
                if lower else 'Vila det som gör ont och kontakta en fysioterapeut om det inte släpper.')
    if severity >= 4:
        return ('Byt fartpass och backar mot kortare lugn löpning på mjukt underlag, stryk hopp. Avbryt om det ökar under passet.'
                if lower else 'Undvik övningar som gör ont, kör resten med lägre vikt.')
    if severity >= 1:
        return ('Spring som planerat men avbryt fartdelen om det blir värre under passet.'
                if lower else 'Träna som vanligt men lyssna på det.')
    return 'Borta.'


def status(entries, today):
    """One row per area and side: latest reading, trend and whether it is active."""
    latest, previous = {}, {}
    for e in sorted(entries or [], key=lambda e: (e.get('date') or '', e.get('ts') or 0)):
        key = (e.get('area'), e.get('side') or '')
        if key in latest:
            previous[key] = latest[key]
        latest[key] = e
    rows = []
    for key, e in latest.items():
        try:
            age = (today - date.fromisoformat(e['date'])).days
        except (KeyError, ValueError):
            continue
        active = e['severity'] > 0 and age <= ACTIVE_DAYS
        before = previous.get(key)
        trend = None
        if before:
            diff = e['severity'] - before['severity']
            trend = 'worse' if diff > 0 else 'better' if diff < 0 else 'same'
        history = [h for h in entries if (h.get('area'), h.get('side') or '') == key]
        first_active = next((h['date'] for h in reversed(history) if h['severity'] == 0), None)
        rows.append({
            'area': key[0], 'side': key[1], 'label': label(*key),
            'severity': e['severity'], 'note': e.get('note') or '', 'date': e['date'],
            'daysSince': age, 'active': active, 'trend': trend,
            'previousSeverity': before['severity'] if before else None,
            'since': min((h['date'] for h in history if h['severity'] > 0 and (not first_active or h['date'] > first_active)),
                         default=e['date']),
            'needsCheckIn': active and age >= CHECK_IN_AFTER_DAYS,
            'advice': advice(e['severity'], key[0]),
        })
    rows.sort(key=lambda r: (not r['active'], -r['severity'], r['label']))
    return rows


def active_areas(rows, min_severity=3):
    """Areas that should take exercises out of the strength programme."""
    return sorted({r['area'] for r in rows if r['active'] and r['severity'] >= min_severity})


def describe(rows):
    active = [r for r in rows if r['active']]
    if not active:
        return 'NIGGLES: none logged as active. Do not invent any.'
    lines = ['ACTIVE NIGGLES (the athlete\'s own 0–10 log; these outrank good watch numbers):']
    for r in active:
        trend = {'worse': ', getting worse', 'better': ', improving', 'same': ', unchanged'}.get(r['trend'], '')
        lines.append(f"  - {r['label']}: {r['severity']}/10 on {r['date']} (since {r['since']}{trend})"
                     + (f" — \"{r['note']}\"" if r['note'] else '')
                     + (f". Not updated for {r['daysSince']} days: ask how it feels now before prescribing hard running."
                        if r['needsCheckIn'] else '.'))
    lines.append('Adapt every running and strength suggestion to these: at 4/10 or more in the lower body, no '
                 'intervals, hills or jumps; at 7/10 or more, no running.')
    return '\n'.join(lines)
