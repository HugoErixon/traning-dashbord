"""Passanalyser: hur lugna pass, tröskelpass och intervaller utvecklas över tid.

Passen sorteras efter Garmins egen träningseffekt (trainingEffectLabel), inte
efter namn — löparen döper inte alltid sina pass, men klockan klassar alla.

- Lugna pass: terrängjusterad fart och snittpuls, och ur dem aerob effektivitet
  (meter per hjärtslag). Samma fart på lägre puls är det tydligaste tecknet på
  att grunden blir bättre, så det är det måttet trenden bygger på.
- Tröskel- och tempopass: farten på arbetsdelarna (Garmins INTERVAL_ACTIVE-
  sammanfattning), inte hela passet med uppvärmning och joggvila.
- Intervaller: samma sak för VO2max- och anaeroba pass. Strukturerade pass
  sorteras om efter repslängd (högst 5 min = intervall), eftersom Garmin ofta
  klassar korta intervaller som tempo.

Lopp hålls utanför; ett lopp är inte ett träningspass att jämföra med.
Rena funktioner — ingen databas, inget nätverk, ingen AI.
"""

from datetime import date, datetime, timedelta

EASY = {'AEROBIC_BASE', 'RECOVERY'}
THRESHOLD = {'LACTATE_THRESHOLD', 'TEMPO'}
INTERVALS = {'VO2MAX', 'ANAEROBIC_CAPACITY', 'SPEED'}
MIN_EASY_KM = 3.0
INTERVAL_REP_MAX_MIN = 5.0
# En förändring mindre än så här är brus i GPS, väder och dagsform.
EFFICIENCY_NOISE = 0.02
PACE_NOISE_SEC = 3.0


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _pace_str(sec):
    if not sec:
        return None
    sec = int(round(sec))
    return f'{sec // 60}:{sec % 60:02d}'


def _active_split(raw):
    for split in raw.get('splitSummaries') or []:
        if isinstance(split, dict) and split.get('splitType') == 'INTERVAL_ACTIVE':
            distance, duration = _num(split.get('distance')), _num(split.get('duration'))
            reps = int(split.get('noOfSplits') or 0)
            # Ett pass utan strukturerade intervaller blir en enda lång
            # "arbetsdel" — det är hela passet, inte arbetsfarten.
            if distance and duration and distance >= 400 and reps >= 2:
                return {'km': distance / 1000, 'seconds': duration,
                        'reps': reps}
    return None


def classify(row):
    raw = row.get('raw') if isinstance(row.get('raw'), dict) else {}
    if 'run' not in str(row.get('type') or '').lower():
        return None
    if (raw.get('eventType') or {}).get('typeKey') == 'race':
        return 'race'
    label = raw.get('trainingEffectLabel')
    if label in EASY:
        return 'easy'
    if label in THRESHOLD:
        return 'threshold'
    if label in INTERVALS:
        return 'intervals'
    return None


def sessions(activities):
    """Alla klassade löppass med de mått varje kategori behöver."""
    out = []
    for row in activities or []:
        kind = classify(row)
        day = _day(row.get('date'))
        raw = row.get('raw') if isinstance(row.get('raw'), dict) else {}
        km = (_num(row.get('distance')) or 0) / 1000
        seconds = _num(row.get('duration'))
        if 'run' not in str(row.get('type') or '').lower() or not day or not seconds or km <= 0:
            continue
        kind = kind or 'other'
        hr = _num(row.get('avg_hr')) or _num(raw.get('averageHR'))
        item = {'date': day.isoformat(), 'kind': kind, 'km': round(km, 2),
                'name': str(row.get('name') or '')[:80], 'hr': round(hr) if hr else None,
                'paceSec': round(seconds / km, 1)}
        if kind == 'easy' and km >= MIN_EASY_KM:
            speed = _num(raw.get('avgGradeAdjustedSpeed')) or _num(raw.get('averageSpeed'))
            if speed:
                item['paceSec'] = round(1000 / speed, 1)
                if hr:
                    item['efficiency'] = round(speed * 60 / hr, 3)
        elif kind in ('threshold', 'intervals'):
            active = _active_split(raw)
            if active:
                # Garmin kallar ofta 400-metersintervaller "tempo". Passets
                # struktur säger mer: korta arbetsdelar är intervaller, långa
                # är tröskel, oavsett vilken effekt klockan räknade fram.
                rep_min = active['seconds'] / 60 / active['reps']
                item['kind'] = 'intervals' if rep_min <= INTERVAL_REP_MAX_MIN else 'threshold'
                item['repMin'] = round(rep_min, 1)
                item['workPaceSec'] = round(active['seconds'] / active['km'], 1)
                item['workKm'] = round(active['km'], 2)
                item['workMin'] = round(active['seconds'] / 60, 1)
                item['reps'] = active['reps']
        out.append(item)
    return sorted(out, key=lambda s: s['date'])


def _split_halves(items, today, days):
    recent_start = today - timedelta(days=days // 2)
    start = today - timedelta(days=days)
    older = [s for s in items if start <= _day(s['date']) < recent_start]
    recent = [s for s in items if _day(s['date']) >= recent_start]
    return older, recent


def _direction(delta, noise, lower_is_better):
    if delta is None:
        return 'unclear'
    if abs(delta) < noise:
        return 'stable'
    better = delta < 0 if lower_is_better else delta > 0
    return 'improving' if better else 'declining'


def easy_summary(items, today, days):
    runs = [s for s in items if s['kind'] == 'easy' and s['km'] >= MIN_EASY_KM
            and _day(s['date']) >= today - timedelta(days=days)]
    older, recent = _split_halves(runs, today, days)
    eff_old = _mean(s.get('efficiency') for s in older)
    eff_new = _mean(s.get('efficiency') for s in recent)
    pace_old, pace_new = _mean(s['paceSec'] for s in older), _mean(s['paceSec'] for s in recent)
    hr_old, hr_new = _mean(s['hr'] for s in older), _mean(s['hr'] for s in recent)
    rel = (eff_new / eff_old - 1) if eff_old and eff_new else None
    summary = {
        'key': 'easy', 'label': 'Lugna pass', 'count': len(runs),
        'direction': _direction(rel, EFFICIENCY_NOISE, lower_is_better=False),
        'series': [{'t': s['date'], 'v': s.get('efficiency'), 'pace': s['paceSec'],
                    'hr': s['hr'], 'km': s['km']} for s in runs if s.get('efficiency')],
        'metric': 'Meter per hjärtslag (terrängjusterad fart / puls)',
        'now': {'paceSec': pace_new, 'hr': hr_new, 'efficiency': eff_new},
        'then': {'paceSec': pace_old, 'hr': hr_old, 'efficiency': eff_old},
        'changePct': round(rel * 100, 1) if rel is not None else None,
    }
    if rel is not None:
        summary['text'] = (
            f'Senaste {days // 2} dagarna: {_pace_str(pace_new)}/km på {round(hr_new)} slag i snitt, '
            f'mot {_pace_str(pace_old)}/km på {round(hr_old)} slag innan. '
            f'Effektiviteten är {abs(summary["changePct"]):.1f} % '
            f'{"bättre" if rel > 0 else "sämre"}.')
    else:
        summary['text'] = 'För få lugna pass i båda halvorna av perioden för att se en riktning.'
    return summary


def _work_summary(items, today, days, kind, label):
    runs = [s for s in items if s['kind'] == kind and s.get('workPaceSec')
            and _day(s['date']) >= today - timedelta(days=days)]
    older, recent = _split_halves(runs, today, days)
    old, new = _mean(s['workPaceSec'] for s in older), _mean(s['workPaceSec'] for s in recent)
    delta = (new - old) if old and new else None
    best = min(runs, key=lambda s: s['workPaceSec']) if runs else None
    summary = {
        'key': kind, 'label': label, 'count': len(runs),
        'direction': _direction(delta, PACE_NOISE_SEC, lower_is_better=True),
        'series': [{'t': s['date'], 'v': s['workPaceSec'], 'workMin': s.get('workMin'),
                    'reps': s.get('reps'), 'hr': s['hr'], 'name': s['name']} for s in runs],
        'metric': 'Fart på arbetsdelarna (s/km), utan uppvärmning och vila',
        'deltaSec': round(delta, 1) if delta is not None else None,
        'latest': runs[-1] if runs else None,
        'best': best,
    }
    if delta is not None:
        summary['text'] = (
            f'Arbetsdelarna går i {_pace_str(new)}/km senaste {days // 2} dagarna mot '
            f'{_pace_str(old)}/km innan ({abs(delta):.0f} s/km {"snabbare" if delta < 0 else "långsammare"}).')
    elif runs:
        summary['text'] = (f'{len(runs)} pass i perioden, senast {_pace_str(runs[-1]["workPaceSec"])}/km '
                           'på arbetsdelarna. Behöver pass i båda halvorna för en riktning.')
    else:
        summary['text'] = 'Inga sådana pass med arbetsdelar i perioden.'
    return summary


def weekly_volume(items, today, weeks=12):
    monday = today - timedelta(days=today.weekday())
    out = []
    for back in range(weeks - 1, -1, -1):
        start = monday - timedelta(weeks=back)
        end = start + timedelta(days=7)
        week = [s for s in items if start <= _day(s['date']) < end]
        hard = [s for s in week if s['kind'] in ('threshold', 'intervals', 'race')]
        out.append({'week': start.isocalendar()[1], 'start': start.isoformat(),
                    'km': round(sum(s['km'] for s in week), 1), 'sessions': len(week),
                    'hard': len(hard), 'current': back == 0})
    return out


def insights(activities, today, days=90):
    items = sessions(activities)
    return {
        'today': today.isoformat(), 'days': days,
        'areas': [easy_summary(items, today, days),
                  _work_summary(items, today, days, 'threshold', 'Tröskel- och tempopass'),
                  _work_summary(items, today, days, 'intervals', 'Intervaller')],
        'weeks': weekly_volume(items, today),
    }


def describe(result):
    """Kort textblock för AI-prompter."""
    lines = []
    for area in result['areas']:
        lines.append(f"- {area['label']} ({area['count']} pass, riktning: {area['direction']}): {area['text']}")
    weeks = result['weeks'][-6:]
    lines.append('- Löpvolym per vecka (senaste 6): ' + ', '.join(
        f"v.{w['week']} {w['km']} km/{w['hard']} hårda" for w in weeks))
    return '\n'.join(lines)
