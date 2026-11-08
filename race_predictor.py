"""Race predictor: en löpande prognos för 5 km, 10 km, halvmaraton och maraton.

Två oberoende källor vägs ihop, i stil med Garmin och Strava men med ett eget
steg som ingen av dem har: kalibrering mot löparens egna lopp.

1. Garmins egen race predictor (daglig historik från Garmin Connect).
2. Tröskelmodellen: Garmins uppmätta mjölksyratröskelfart, snittad över sju
   dagar eftersom Garmin hoppar mellan två värden, räknad till halvmaratonfart
   och vidare till övriga distanser med Riegels formel.

Varje källa jämförs med de lopp som finns i historiken (Garmins eventType
"race"). Har en källa legat för optimistiskt eller pessimistiskt mot dina
riktiga lopptider rättas den med samma kvot, inom ±6 %. Det är en empirisk
justering, inte fysiologi, och den visas öppet i svaret.

Allt är rena funktioner utan databas, nätverk eller AI. Prognosen räknas om
vid varje anrop ur senaste synkade data, och en kurva byggs genom att räkna
samma prognos bakåt dag för dag med bara det som fanns den dagen.
"""

from datetime import date, datetime, timedelta

DISTANCES = (('5k', 5.0, '5 km'), ('10k', 10.0, '10 km'),
             ('half', 21.0975, 'Halvmaraton'), ('marathon', 42.195, 'Maraton'))
KM = {key: km for key, km, _ in DISTANCES}
LABELS = {key: label for key, _, label in DISTANCES}
GARMIN_FIELDS = {'5k': 'time5K', '10k': 'time10K', 'half': 'timeHalfMarathon',
                 'marathon': 'timeMarathon'}

RIEGEL_EXPONENT = 1.06
# Deklarerat antagande: halvmaratonfart ligger ungefär 3 % långsammare än
# mjölksyratröskelfarten. Kalibreringen mot egna lopp rättar resten.
THRESHOLD_TO_HALF = 1.03
CALIBRATION_LIMIT = 0.06
CALIBRATION_MAX_AGE_DAYS = 400
GARMIN_MAX_AGE_DAYS = 14
THRESHOLD_MAX_AGE_DAYS = 21
WEIGHTS = {'garmin': 0.5, 'threshold': 0.5}
SOURCE_LABELS = {'garmin': 'Garmins prognos', 'threshold': 'Din tröskelfart'}


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


def riegel(seconds, from_km, to_km):
    return seconds * (to_km / from_km) ** RIEGEL_EXPONENT


def _all_from(key, seconds):
    return {k: riegel(seconds, KM[key], KM[k]) for k in KM}


def clean_garmin(entries):
    """Garmins dagliga prognoser → {datum: {distans: sekunder}}."""
    out = {}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        day = _day(entry.get('calendarDate'))
        times = {k: _num(entry.get(field)) for k, field in GARMIN_FIELDS.items()}
        if day and all(times.values()):
            out[day] = times
    return out


def clean_threshold(metric_rows):
    """metric_history → sorterad lista (datum, tröskelfart s/km)."""
    points = {}
    for row in metric_rows or []:
        day = _day(row.get('date'))
        pace = _num(row.get('lactate_pace'))
        if day and pace and 150 <= pace <= 600:
            points[day] = pace
    return sorted(points.items())


def races_from_activities(activities):
    """Lopp ur aktivitetshistoriken: Garmins eventType 'race' på löppass."""
    races = []
    for row in activities or []:
        raw = row.get('raw') or {}
        if not isinstance(raw, dict):
            continue
        if (raw.get('eventType') or {}).get('typeKey') != 'race':
            continue
        if 'run' not in str(row.get('type') or '').lower():
            continue
        km = (_num(row.get('distance')) or 0) / 1000
        seconds = _num(row.get('duration'))
        day = _day(row.get('date'))
        if not day or not seconds or km < 3:
            continue
        races.append({'date': day, 'km': round(km, 3), 'seconds': round(seconds),
                      'name': str(row.get('name') or 'Lopp')[:80]})
    return sorted(races, key=lambda r: r['date'])


def _garmin_at(garmin, day):
    for offset in range(GARMIN_MAX_AGE_DAYS + 1):
        times = garmin.get(day - timedelta(days=offset))
        if times:
            return times, day - timedelta(days=offset)
    return None, None


def _threshold_at(threshold, day):
    recent = [(d, p) for d, p in threshold if day - timedelta(days=6) <= d <= day]
    if not recent:
        older = [(d, p) for d, p in threshold if d <= day]
        if not older or (day - older[-1][0]).days > THRESHOLD_MAX_AGE_DAYS:
            return None, None
        recent = [older[-1]]
    return sum(p for _, p in recent) / len(recent), recent[-1][0]


def raw_sources(day, garmin, threshold):
    """Okalibrerade prognoser per källa för en given dag."""
    sources = {}
    times, garmin_day = _garmin_at(garmin, day)
    if times:
        sources['garmin'] = {'times': dict(times), 'asOf': garmin_day}
    pace, pace_day = _threshold_at(threshold, day)
    if pace:
        half = pace * KM['half'] * THRESHOLD_TO_HALF
        sources['threshold'] = {'times': _all_from('half', half), 'asOf': pace_day,
                                'paceSec': round(pace, 1)}
    return sources


def calibration(races, garmin, threshold, today):
    """Kvot riktig lopptid / prognos per källa, ur lopp de senaste 400 dagarna."""
    factors = {}
    used = []
    for race in races:
        if (today - race['date']).days > CALIBRATION_MAX_AGE_DAYS:
            continue
        # Prognosen från dagen före loppet, så att loppet självt inte påverkar den.
        sources = raw_sources(race['date'] - timedelta(days=1), garmin, threshold)
        for name, source in sources.items():
            predicted = riegel(source['times']['half'], KM['half'], race['km'])
            factors.setdefault(name, []).append(race['seconds'] / predicted)
        if sources:
            used.append(race)
    result = {}
    for name, values in factors.items():
        mean = sum(values) / len(values)
        result[name] = max(1 - CALIBRATION_LIMIT, min(1 + CALIBRATION_LIMIT, mean))
    return result, used


def predict_on(day, garmin, threshold, factors):
    """Kalibrerad, sammanvägd prognos för en dag, eller None utan källor."""
    sources = raw_sources(day, garmin, threshold)
    if not sources:
        return None
    total = sum(WEIGHTS[name] for name in sources)
    combined = {}
    for key in KM:
        combined[key] = sum(source['times'][key] * factors.get(name, 1.0) * WEIGHTS[name]
                            for name, source in sources.items()) / total
    return {'times': combined, 'sources': sources}


def _fmt(seconds):
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def _pace(seconds, km):
    per = int(round(seconds / km))
    return f'{per // 60}:{per % 60:02d}/km'


def predictor(garmin_entries, metric_rows, activities, today, target=None, history_days=180):
    """Hela svaret för /api/race-predictor.

    `target` är {'distance': '5k'|'10k'|'half'|'marathon', 'seconds': int,
    'deadline': 'ÅÅÅÅ-MM-DD' eller None}."""
    garmin = clean_garmin(garmin_entries)
    threshold = clean_threshold(metric_rows)
    races = races_from_activities(activities)
    factors, calibration_races = calibration(races, garmin, threshold, today)

    current = predict_on(today, garmin, threshold, factors)
    if not current:
        return {'available': False, 'today': today.isoformat(),
                'reason': 'Ingen prognos ännu: Garmin har varken skickat en loppprognos '
                          'eller en tröskelfart de senaste veckorna.'}

    history = []
    for offset in range(history_days, -1, -1):
        day = today - timedelta(days=offset)
        point = predict_on(day, garmin, threshold, factors)
        if point:
            history.append({'t': day.isoformat(),
                            **{k: round(v) for k, v in point['times'].items()}})

    def change(days):
        then = next((p for p in history if p['t'] >= (today - timedelta(days=days)).isoformat()), None)
        if not then or then['t'] == today.isoformat():
            return None
        return {'days': days, 'from': then['t'],
                **{k: round(current['times'][k] - then[k]) for k in KM}}

    sources = []
    for name, source in current['sources'].items():
        factor = factors.get(name, 1.0)
        sources.append({
            'key': name, 'label': SOURCE_LABELS[name], 'weight': WEIGHTS[name],
            'asOf': source['asOf'].isoformat(),
            'calibration': round(factor, 4),
            'half': round(source['times']['half'] * factor),
            'raw': {k: round(v) for k, v in source['times'].items()},
            **({'thresholdPaceSec': source['paceSec']} if 'paceSec' in source else {}),
        })
    spread = [s['half'] for s in sources]

    result = {
        'available': True,
        'today': today.isoformat(),
        'predictions': [{'key': k, 'label': LABELS[k], 'km': KM[k],
                         'seconds': round(current['times'][k]),
                         'time': _fmt(current['times'][k]),
                         'pace': _pace(current['times'][k], KM[k])} for k in KM],
        'range': {'half': [min(spread), max(spread)]} if len(spread) > 1 else None,
        'sources': sources,
        'calibration': {
            'races': [{'date': r['date'].isoformat(), 'name': r['name'], 'km': r['km'],
                       'time': _fmt(r['seconds'])} for r in calibration_races],
            'note': ('Justerad mot dina egna lopp.' if calibration_races
                     else 'Inga lopp i historiken ännu, så prognosen är inte kalibrerad mot dig.'),
        },
        'change28': change(28),
        'change90': change(90),
        'history': history,
        'method': ('Garmins prognos och din tröskelfart (7-dagarssnitt, HM-fart = tröskelfart '
                   '× 1,03), 50/50, kalibrerade mot dina lopp. Andra distanser via Riegels '
                   'formel. Räknas om varje gång ny data synkas.'),
    }
    if target and target.get('distance') in KM and _num(target.get('seconds')):
        key, goal = target['distance'], float(target['seconds'])
        predicted = current['times'][key]
        gap = predicted - goal
        info = {'distance': key, 'label': LABELS[key], 'seconds': round(goal),
                'time': _fmt(goal), 'pace': _pace(goal, KM[key]),
                'gapSec': round(gap), 'reached': gap <= 0,
                'paceGapSec': round(gap / KM[key], 1)}
        deadline = _day(target.get('deadline'))
        if deadline and deadline > today:
            weeks = (deadline - today).days / 7
            info['deadline'] = deadline.isoformat()
            info['weeksLeft'] = round(weeks, 1)
            info['neededPerWeekSec'] = round(max(gap, 0) / weeks, 1) if weeks else None
            recent = change(28)
            if recent:
                info['recentPerWeekSec'] = round(-recent[key] / 4, 1)
        result['target'] = info
    return result
