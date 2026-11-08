"""Race predictor: Trainyzes egen prognos för 5 km, 10 km, halvmaraton och maraton.

Prognosen bygger inte på Garmins prognos. Den räknas ur löparens egna varv:

1. Varje varv på minst sex minuter ger en punkt (snittpuls, terrängjusterad
   fart). Terrängjusteringen (Garmins GAP) gör att ett backigt pass inte ser
   långsammare ut än det var.
   Intervalldrag på 2–6 minuter tas också med, men med pulsen i slutet av
   draget (varvets maxpuls minus tre slag) i stället för snittet: i ett kort
   drag hinner pulsen inte ikapp farten, så snittpulsen får hög fart att se
   billig ut. Det är slutpulsen som visar vad farten kostar. Bara drag som är
   tydligt snabbare än löparens vanliga varv räknas — en jogg- eller gångvila
   märkt "aktiv" är ingen intervall. Kortare drag än två minuter hoppas över;
   där hinner inte ens slutpulsen upp.
2. Varven från de senaste sex veckorna vägs ihop i en linje fart = a + b·puls,
   där nyare och längre varv väger tyngre (halveringstid tre veckor).
3. Halvmaratonfarten är linjens fart vid 97 % av tröskelpulsen. Det är ett
   deklarerat antagande: ett halvmaraton springs strax under tröskeln.
   Tröskelpulsen är klockans uppmätta (eller ett labbvärde när det finns).
4. Övriga distanser räknas från halvmaraton med Riegels formel.

Linjen får aldrig dras långt ut i det okända: utan minst 20 stabila varv och
fyra punkter nära tröskelpulsen, eller med en osäkerhet över 2,5 minuter, ges
ingen egen prognos den dagen. Lopp justerar inget — ett varmt eller backigt lopp ska inte
flytta prognosen. De visas i stället som en kontroll av vad modellen sa dagen
innan. Garmins egen prognos visas bara som jämförelse.

Rena funktioner utan databas, nätverk eller AI.
"""

import math
from datetime import date, datetime, timedelta

DISTANCES = (('5k', 5.0, '5 km'), ('10k', 10.0, '10 km'),
             ('half', 21.0975, 'Halvmaraton'), ('marathon', 42.195, 'Maraton'))
KM = {key: km for key, km, _ in DISTANCES}
LABELS = {key: label for key, _, label in DISTANCES}
GARMIN_FIELDS = {'5k': 'time5K', '10k': 'time10K', 'half': 'timeHalfMarathon',
                 'marathon': 'timeMarathon'}

RIEGEL_EXPONENT = 1.06
WINDOW_DAYS = 42
HALF_LIFE_DAYS = 21
MIN_LAP_SECONDS = 360
MIN_LAP_METERS = 500
MIN_LAP_HR = 120
MIN_REP_SECONDS = 120
REP_MAX_HR_OFFSET = 3
# Ett drag måste vara minst så här mycket snabbare än medianen av de stabila
# varven för att räknas som intervall.
REP_MIN_SPEED_RATIO = 1.1
# Maxpuls som ligger orimligt långt över varvets snitt är en sensorspik.
REP_MAX_HR_ABOVE_AVG = 20
# Halvmaraton springs strax under tröskeln; deklarerat antagande.
HALF_HR_FRACTION = 0.97
# Varv inom så här många slag under målpulsen räknas som "nära tröskeln".
NEAR_TARGET_BPM = 8
MIN_NEAR_LAPS = 4
MAX_HALF_SE_SECONDS = 150
# Intervalldragen kompletterar, men grunden måste vara stabila varv.
MIN_STEADY_LAPS = 20
LTHR_MAX_AGE_DAYS = 60
GARMIN_MAX_AGE_DAYS = 14
# Garmins tröskelfart × 1,03 ≈ HM-fart; bara som jämförelse.
THRESHOLD_TO_HALF = 1.03


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


def _is_race(row):
    raw = row.get('raw') if isinstance(row.get('raw'), dict) else {}
    return (raw.get('eventType') or {}).get('typeKey') == 'race'


def lap_points(activities):
    """Stabila varv och intervalldrag: [(datum, puls, fart m/s, minuter, 'lap'|'rep')].

    Lopp hålls utanför modellen. Varje varv är [sekunder, meter, snittpuls, fart,
    typ, maxpuls]; äldre sparade varv saknar maxpuls och ger då bara stabila varv."""
    steady, reps = [], []
    for row in activities or []:
        if _is_race(row) or 'run' not in str(row.get('type') or '').lower():
            continue
        if 'treadmill' in str(row.get('type') or ''):
            continue
        day = _day(row.get('date'))
        if not day:
            continue
        for lap in row.get('laps') or []:
            if not isinstance(lap, (list, tuple)) or len(lap) < 4:
                continue
            seconds, meters, hr, speed = (_num(lap[0]), _num(lap[1]), _num(lap[2]), _num(lap[3]))
            if not (seconds and meters and hr and speed and 1.5 < speed < 7):
                continue
            if seconds >= MIN_LAP_SECONDS and meters >= MIN_LAP_METERS and hr >= MIN_LAP_HR:
                steady.append((day, hr, speed, seconds / 60, 'lap'))
                continue
            kind = lap[4] if len(lap) > 4 else None
            max_hr = _num(lap[5]) if len(lap) > 5 else None
            if (MIN_REP_SECONDS <= seconds < MIN_LAP_SECONDS and meters >= 400
                    and kind in ('ACTIVE', 'INTERVAL') and max_hr
                    and hr < max_hr <= hr + REP_MAX_HR_ABOVE_AVG):
                reps.append((day, max_hr - REP_MAX_HR_OFFSET, speed, seconds / 60, 'rep'))
    if steady and reps:
        speeds = sorted(p[2] for p in steady)
        floor = speeds[len(speeds) // 2] * REP_MIN_SPEED_RATIO
        reps = [r for r in reps if r[2] >= floor]
    else:
        reps = []
    return sorted(steady + reps)


def threshold_hr_series(metric_rows):
    out = {}
    for row in metric_rows or []:
        day, hr = _day(row.get('date')), _num(row.get('lactate_hr'))
        if day and hr and 140 <= hr <= 215:
            out[day] = hr
    return sorted(out.items())


def _lthr_at(series, day, override=None):
    if override:
        return override
    older = [(d, hr) for d, hr in series if d <= day]
    if not older or (day - older[-1][0]).days > LTHR_MAX_AGE_DAYS:
        return None
    return older[-1][1]


def model_on(day, points, lthr_series, lthr_override=None):
    """Egen prognos för en dag, eller {'available': False, 'reason': ...}."""
    lthr = _lthr_at(lthr_series, day, lthr_override)
    if not lthr:
        return {'available': False, 'reason': 'Ingen tröskelpuls uppmätt de senaste två månaderna.'}
    target_hr = lthr * HALF_HR_FRACTION
    window = [p[:4] for p in points if 0 <= (day - p[0]).days <= WINDOW_DAYS]
    reps = sum(1 for p in points if len(p) > 4 and p[4] == 'rep' and 0 <= (day - p[0]).days <= WINDOW_DAYS)
    near = sum(1 for _, hr, _, _ in window if hr >= target_hr - NEAR_TARGET_BPM)
    if len(window) - reps < MIN_STEADY_LAPS or near < MIN_NEAR_LAPS:
        return {'available': False, 'laps': len(window), 'nearLaps': near,
                'reason': f'För lite underlag de senaste sex veckorna: {len(window) - reps} stabila varv '
                          f'(minst {MIN_STEADY_LAPS}) och {near} nära tröskelpulsen (minst {MIN_NEAR_LAPS}).'}
    weights = [math.exp(-(day - d).days / HALF_LIFE_DAYS) * minutes for d, _, _, minutes in window]
    total = sum(weights)
    mean_hr = sum(w * hr for w, (_, hr, _, _) in zip(weights, window)) / total
    mean_sp = sum(w * sp for w, (_, _, sp, _) in zip(weights, window)) / total
    sxx = sum(w * (hr - mean_hr) ** 2 for w, (_, hr, _, _) in zip(weights, window))
    if sxx <= 0:
        return {'available': False, 'reason': 'Pulsen varierar för lite mellan varven.'}
    slope = sum(w * (hr - mean_hr) * (sp - mean_sp) for w, (_, hr, sp, _) in zip(weights, window)) / sxx
    intercept = mean_sp - slope * mean_hr
    speed = intercept + slope * target_hr
    if slope <= 0 or speed <= 1.5:
        return {'available': False, 'reason': 'Farten och pulsen hänger inte ihop i datan just nu.'}
    resid = math.sqrt(sum(w * (sp - (intercept + slope * hr)) ** 2
                          for w, (_, hr, sp, _) in zip(weights, window)) / total)
    n_eff = total ** 2 / sum(w * w for w in weights)
    se_speed = resid * math.sqrt(1 / n_eff + (target_hr - mean_hr) ** 2 / (sxx / total * n_eff))
    half = KM['half'] * 1000 / speed
    slow = KM['half'] * 1000 / max(speed - se_speed, 0.5)
    se_half = slow - half
    if se_half > MAX_HALF_SE_SECONDS:
        return {'available': False, 'laps': len(window), 'nearLaps': near,
                'reason': 'Osäkerheten är för stor just nu — varven pekar åt olika håll.'}
    return {
        'available': True, 'half': half, 'seHalf': se_half,
        'times': _all_from('half', half),
        'lthr': round(lthr), 'targetHr': round(target_hr, 1),
        'paceSec': round(1000 / speed, 1), 'laps': len(window) - reps, 'reps': reps, 'nearLaps': near,
        'secPerBeat': round(1000 / (intercept + slope * (target_hr - 1)) - 1000 / speed, 2),
        'easyPaceAt140': round(1000 / (intercept + slope * 140), 1) if intercept + slope * 140 > 1.5 else None,
    }


def clean_garmin(entries):
    out = {}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        day = _day(entry.get('calendarDate'))
        times = {k: _num(entry.get(field)) for k, field in GARMIN_FIELDS.items()}
        if day and all(times.values()):
            out[day] = times
    return out


def _garmin_at(garmin, day):
    for offset in range(GARMIN_MAX_AGE_DAYS + 1):
        times = garmin.get(day - timedelta(days=offset))
        if times:
            return times
    return None


def _threshold_half(metric_rows, day):
    paces = [(_day(r.get('date')), _num(r.get('lactate_pace'))) for r in metric_rows or []]
    recent = [p for d, p in paces if d and p and day - timedelta(days=6) <= d <= day]
    if not recent:
        return None
    return sum(recent) / len(recent) * KM['half'] * THRESHOLD_TO_HALF


def races_from_activities(activities):
    races = []
    for row in activities or []:
        if not _is_race(row) or 'run' not in str(row.get('type') or '').lower():
            continue
        km = (_num(row.get('distance')) or 0) / 1000
        seconds = _num(row.get('duration'))
        day = _day(row.get('date'))
        if not day or not seconds or km < 3:
            continue
        raw = row.get('raw') if isinstance(row.get('raw'), dict) else {}
        gap = _num(raw.get('avgGradeAdjustedSpeed'))
        races.append({'date': day, 'km': km, 'seconds': seconds,
                      'gapSeconds': km * 1000 / gap if gap else None,
                      'name': str(row.get('name') or 'Lopp')[:80]})
    return sorted(races, key=lambda r: r['date'])


def _fmt(seconds):
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def _pace(seconds, km):
    per = int(round(seconds / km))
    return f'{per // 60}:{per % 60:02d}/km'


def predictor(garmin_entries, metric_rows, activities, today, target=None,
              history_days=120, lthr_override=None):
    """Hela svaret för /api/race-predictor.

    `activities` är löppass med `laps` (se lap_points) och `raw`. `target` är
    {'distance', 'seconds', 'deadline'}."""
    points = lap_points(activities)
    lthr_series = threshold_hr_series(metric_rows)
    garmin = clean_garmin(garmin_entries)
    current = model_on(today, points, lthr_series, lthr_override)

    history = []
    for offset in range(history_days, -1, -1):
        day = today - timedelta(days=offset)
        point = model_on(day, points, lthr_series, lthr_override)
        g = _garmin_at(garmin, day)
        if point['available'] or g:
            entry = {'t': day.isoformat()}
            if point['available']:
                entry.update({k: round(v) for k, v in point['times'].items()})
            if g:
                entry['garmin'] = {k: round(v) for k, v in g.items()}
            history.append(entry)

    garmin_now = _garmin_at(garmin, today)
    threshold_half = _threshold_half(metric_rows, today)
    comparisons = []
    if garmin_now:
        comparisons.append({'key': 'garmin', 'label': 'Garmins prognos', 'half': round(garmin_now['half'])})
    if threshold_half:
        comparisons.append({'key': 'threshold', 'label': 'Garmins tröskelfart × 1,03',
                            'half': round(threshold_half)})

    checks = []
    for race in races_from_activities(activities):
        before = model_on(race['date'] - timedelta(days=1), points, lthr_series, lthr_override)
        checks.append({
            'date': race['date'].isoformat(), 'name': race['name'], 'km': round(race['km'], 2),
            'time': _fmt(race['seconds']),
            'gapTime': _fmt(race['gapSeconds']) if race['gapSeconds'] else None,
            'predicted': _fmt(riegel(before['half'], KM['half'], race['km'])) if before['available'] else None,
        })

    method = ('Trainyzes egen modell: dina varv på minst 6 minuter och dina intervalldrag på '
              '2–6 minuter (med pulsen i slutet av draget) från de senaste 6 veckorna — puls mot '
              'terrängjusterad fart, nyare väger tyngre — läst vid 97 % av din '
              'tröskelpuls. Andra distanser via Riegels formel. Garmins prognos ingår inte, '
              'och lopp justerar inget.')
    if not current['available']:
        return {'available': False, 'today': today.isoformat(), 'reason': current['reason'],
                'history': history, 'comparisons': comparisons, 'checks': checks, 'method': method}

    def change(days):
        start = (today - timedelta(days=days)).isoformat()
        then = next((p for p in history if p['t'] >= start and 'half' in p), None)
        if not then or then['t'] == today.isoformat():
            return None
        return {'days': days, 'from': then['t'],
                **{k: round(current['times'][k] - then[k]) for k in KM}}

    result = {
        'available': True,
        'today': today.isoformat(),
        'predictions': [{'key': k, 'label': LABELS[k], 'km': KM[k],
                         'seconds': round(current['times'][k]),
                         'time': _fmt(current['times'][k]),
                         'pace': _pace(current['times'][k], KM[k])} for k in KM],
        'range': {'half': [round(current['half'] - current['seHalf']),
                           round(current['half'] + current['seHalf'])]},
        'model': {k: current[k] for k in ('lthr', 'targetHr', 'paceSec', 'laps', 'reps', 'nearLaps',
                                          'secPerBeat', 'easyPaceAt140')},
        'comparisons': comparisons,
        'checks': checks,
        'change28': change(28),
        'change90': change(90),
        'history': history,
        'method': method,
    }
    if target and target.get('distance') in KM and _num(target.get('seconds')):
        key, goal = target['distance'], float(target['seconds'])
        gap = current['times'][key] - goal
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
