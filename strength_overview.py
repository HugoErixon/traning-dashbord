"""Styrkesidan: progress över tid och underlag för att logga passet live.

Rena funktioner utan databas, så att de går att testa. Varje rad i
`strength_exercises` är "övning, antal set, reps, vikt" — när man loggar set
för set slås likadana set i följd ihop till en rad (3 × 10 @ 40 kg), så
historiken ser likadan ut oavsett hur den skrevs in.

Styrka jämförs med estimerad 1RM (Epley: vikt × (1 + reps/30)) från passets
bästa set. Volym är set × reps × vikt; set utan vikt (kroppsvikt) ger ingen
volym men räknas som set.
"""
import re
from datetime import date, datetime, timedelta

DATE_KEY = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def rep_count(reps):
    """Första talet i reps-fältet ("10", "8-10", "30 sek") eller None."""
    if reps is None:
        return None
    match = re.search(r'\d+(?:[,.]\d+)?', str(reps))
    return float(match.group(0).replace(',', '.')) if match else None


def e1rm(weight, reps):
    if not weight or not reps or reps <= 0 or reps > 30:
        return None
    return round(float(weight) * (1 + reps / 30), 1)


def exercise_key(name):
    return re.sub(r'\s+', ' ', str(name or '').strip().lower())


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _day(session_id, activity_dates, created_at, today):
    sid = str(session_id)
    if sid in activity_dates:
        return activity_dates[sid]
    if DATE_KEY.match(sid):
        return sid
    try:
        return datetime.fromtimestamp(float(created_at)).date().isoformat()
    except (TypeError, ValueError, OSError):
        return today.isoformat()


def normalize_rows(rows, activity_dates, today):
    """DB-rader (dict med id, session_id, exercise, sets, reps, weight, note,
    created_at) → poster med datum, volym och e1RM, sorterade i loggordning."""
    entries = []
    for row in rows:
        name = str(row.get('exercise') or '').strip()
        if not name:
            continue
        sets = max(1, int(_num(row.get('sets')) or 1))
        reps = rep_count(row.get('reps'))
        weight = _num(row.get('weight'))
        entries.append({
            'id': row.get('id'),
            'sessionId': str(row.get('session_id')),
            'date': _day(row.get('session_id'), activity_dates, row.get('created_at'), today),
            'exercise': name,
            'key': exercise_key(name),
            'sets': sets,
            'reps': str(row.get('reps') or ''),
            'repCount': reps,
            'weight': weight,
            'note': row.get('note') or '',
            'volume': round(sets * reps * weight, 1) if reps and weight else 0,
            'e1rm': e1rm(weight, reps),
            'createdAt': _num(row.get('created_at')) or 0,
        })
    entries.sort(key=lambda e: (e['date'], e['createdAt'], e['id'] or 0))
    return entries


def _set_label(entry):
    reps = entry['reps'] or '–'
    weight = f" @ {entry['weight']:g} kg" if entry['weight'] else ''
    return f"{entry['sets']}×{reps}{weight}"


def _sessions(entries):
    """Grupperar poster per pass, i datumordning."""
    by_session = {}
    for entry in entries:
        by_session.setdefault((entry['date'], entry['sessionId']), []).append(entry)
    return [(day, sid, rows) for (day, sid), rows in sorted(by_session.items())]


def _exercise_sessions(entries):
    """{övningsnyckel: [(datum, sessionId, rader), ...]} i datumordning."""
    result = {}
    for day, sid, rows in _sessions(entries):
        grouped = {}
        for entry in rows:
            grouped.setdefault(entry['key'], []).append(entry)
        for key, ex_rows in grouped.items():
            result.setdefault(key, []).append((day, sid, ex_rows))
    return result


def _session_point(day, rows):
    best = max((r for r in rows if r['e1rm']), key=lambda r: r['e1rm'], default=None)
    top = max(rows, key=lambda r: (r['weight'] or 0, r['repCount'] or 0))
    return {
        't': day,
        'e1rm': best['e1rm'] if best else None,
        'topWeight': top['weight'],
        'topReps': top['reps'],
        'sets': sum(r['sets'] for r in rows),
        'volume': round(sum(r['volume'] for r in rows), 1),
        'label': ', '.join(_set_label(r) for r in rows),
    }


def last_performance(entries, before=None):
    """Senaste passets set per övning, för att fylla i nästa set."""
    result = {}
    for key, sessions in _exercise_sessions(entries).items():
        past = [s for s in sessions if before is None or s[0] < before]
        if not past:
            continue
        day, _, rows = past[-1]
        last = rows[-1]
        result[key] = {
            'exercise': rows[0]['exercise'], 'date': day,
            'label': ', '.join(_set_label(r) for r in rows),
            'weight': last['weight'], 'reps': last['reps'],
        }
    return result


def overview(entries, today, days=90, session_titles=None):
    """Hela svaret för /api/strength/overview (utom dagens passnyckel)."""
    session_titles = session_titles or {}
    start = (today - timedelta(days=days)).isoformat()
    prev_start = (today - timedelta(days=2 * days)).isoformat()
    sessions = _sessions(entries)
    window = [s for s in sessions if s[0] >= start]
    previous = [s for s in sessions if prev_start <= s[0] < start]

    def volume(group):
        return round(sum(r['volume'] for _, _, rows in group for r in rows), 1)

    def set_total(group):
        return sum(r['sets'] for _, _, rows in group for r in rows)

    # Veckor: alla veckor i perioden, även tomma, så att ett uppehåll syns.
    monday = today - timedelta(days=today.weekday())
    first = date.fromisoformat(start)
    first -= timedelta(days=first.weekday())
    weeks = []
    week = first
    while week <= monday:
        weeks.append({'weekStart': week.isoformat(), 'volume': 0, 'sessions': 0, 'sets': 0})
        week += timedelta(days=7)
    index = {w['weekStart']: w for w in weeks}
    for day, _, rows in window:
        d = date.fromisoformat(day)
        w = index.get((d - timedelta(days=d.weekday())).isoformat())
        if w:
            w['volume'] = round(w['volume'] + sum(r['volume'] for r in rows), 1)
            w['sets'] += sum(r['sets'] for r in rows)
            w['sessions'] += 1

    exercises, records = [], []
    for key, ex_sessions in _exercise_sessions(entries).items():
        points = [_session_point(day, rows) for day, _, rows in ex_sessions]
        in_window = [p for p in points if p['t'] >= start]
        if not in_window:
            continue
        best_before = max((p['e1rm'] or 0 for p in points if p['t'] < start), default=0)
        all_best = max((p['e1rm'] or 0 for p in points), default=0)
        scored = [p for p in in_window if p['e1rm']]
        change = round(scored[-1]['e1rm'] - scored[0]['e1rm'], 1) if len(scored) >= 2 else None
        # Ett rekord är när ett pass slår allt som loggats före det.
        running = best_before
        for p in in_window:
            if p['e1rm'] and p['e1rm'] > running:
                if running:
                    records.append({'exercise': ex_sessions[0][2][0]['exercise'], 'date': p['t'],
                                    'e1rm': p['e1rm'], 'previous': running, 'label': p['label']})
                running = p['e1rm']
        exercises.append({
            'key': key,
            'exercise': ex_sessions[-1][2][0]['exercise'],
            'sessions': len(in_window),
            'series': in_window,
            'current': scored[-1]['e1rm'] if scored else None,
            'best': all_best or None,
            'change': change,
            'last': in_window[-1],
            'weighted': bool(scored),
        })
    exercises.sort(key=lambda e: (e['sessions'], e['last']['t']), reverse=True)

    recent = []
    for day, sid, rows in reversed(window):
        recent.append({
            'sessionId': sid, 'date': day, 'title': session_titles.get(sid),
            'volume': round(sum(r['volume'] for r in rows), 1),
            'sets': sum(r['sets'] for r in rows),
            'rows': [{'id': r['id'], 'exercise': r['exercise'], 'sets': r['sets'], 'reps': r['reps'],
                      'weight': r['weight'], 'note': r['note']} for r in rows],
        })

    names = {}
    for entry in entries:
        names[entry['key']] = entry['exercise']
    frequent = sorted(names, key=lambda k: sum(1 for e in entries if e['key'] == k), reverse=True)

    return {
        'days': days,
        'today': today.isoformat(),
        'summary': {
            'sessions': len(window), 'previousSessions': len(previous),
            'volume': volume(window), 'previousVolume': volume(previous),
            'sets': set_total(window), 'previousSets': set_total(previous),
            'records': len(records),
            'lastDate': sessions[-1][0] if sessions else None,
            'perWeek': round(len(window) / max(1, days / 7), 1),
        },
        'weeks': weeks,
        'exercises': exercises,
        'records': sorted(records, key=lambda r: r['date'], reverse=True)[:8],
        'sessions': recent,
        'exerciseNames': [names[k] for k in frequent],
        'last': last_performance(entries, before=today.isoformat()),
    }


def merge_target(session_rows, exercise, reps, weight):
    """Raden som ett nytt set ska slås ihop med, eller None.

    Bara om passets senast loggade rad är samma övning med samma vikt och
    reps — annars blir ordningen i passet fel (3 × bänk, 1 × rodd, 1 × bänk
    är inte 4 × bänk följt av rodd)."""
    if not session_rows:
        return None
    last = max(session_rows, key=lambda r: (r.get('created_at') or 0, r.get('id') or 0))
    same_weight = (_num(last.get('weight')) or 0) == (_num(weight) or 0)
    if exercise_key(last.get('exercise')) == exercise_key(exercise) \
            and str(last.get('reps') or '').strip() == str(reps).strip() and same_weight:
        return last
    return None
