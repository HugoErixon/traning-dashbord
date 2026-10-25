"""Exploratory training scenarios, never a physiological ceiling or a plan.

Fit weekly Garmin VO2max against preceding 28-day running volume. Only expose
the association when a chronological holdout beats last-value persistence.
This is observational, not a causal dose-response estimate. Sparse/flat/noisy
history falls back to a user-supplied hypothetical change, never a population
coefficient disguised as personal evidence. No additional dependencies or I/O.
"""

import math
from datetime import date, timedelta
from statistics import mean, median


RUN_TYPES = {'running', 'trail_running', 'treadmill_running', 'track_running',
             'indoor_running', 'ultra_run', 'virtual_run'}
DISTANCES = ((5, '5 km'), (10, '10 km'), (21.0975, 'Halvmaraton'), (42.195, 'Maraton'))


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _fit(points):
    xs = [math.log1p(p['km']) for p in points]
    ys = [p['vo2max'] for p in points]
    mx, my = mean(xs), mean(ys)
    variance = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / max(variance, 1e-9)
    return my - slope * mx, slope


def _model(points):
    result = {'available': False, 'samples': len(points), 'mae': None,
              'persistenceMae': None, 'slope': None,
              'reason': 'Det behövs minst 16 veckopunkter med VO₂max och föregående fyra veckors löphistorik.'}
    if len(points) < 16:
        return result
    # A four-week gap reduces leakage from overlapping 28-day volume windows.
    train, test = points[:-8], points[-4:]
    if (day(test[0]['date']) - day(train[-1]['date'])).days < 28:
        return result
    volumes = [p['km'] for p in train]
    if max(volumes) - min(volumes) < max(10, mean(volumes) * .3):
        result['reason'] = 'Löpmängden har varierat för lite för att skilja träningssvar från mätbrus.'
        return result
    intercept, slope = _fit(train)
    errors, persistence = [], []
    for i, point in enumerate(test):
        errors.append(abs(intercept + slope * math.log1p(point['km']) - point['vo2max']))
        previous = points[len(points) - 4 + i - 1]
        persistence.append(abs(previous['vo2max'] - point['vo2max']))
    mae, naive = mean(errors), mean(persistence)
    result.update(mae=round(mae, 2), persistenceMae=round(naive, 2))
    if slope <= 0 or naive < .1 or mae >= naive * .9:
        result['reason'] = 'Historikmodellen ger inte en tillräckligt bättre prognos än att behålla senaste VO₂max. Din träningsrespons är därför ännu okänd.'
        return result
    _, slope = _fit(points)
    if slope <= 0:
        result['reason'] = 'Sambandet är inte stabilt över hela perioden.'
        return result
    result.update(available=True, slope=slope, minKm=round(min(p['km'] for p in points), 1),
                  maxKm=round(max(p['km'] for p in points), 1),
                  reason='Ett positivt samband klarade en kontroll mot fyra senare veckopunkter. Det visar samvariation, inte vad fler kilometer orsakar.')
    return result


def race_scenarios(distance, seconds, current_vo2, end):
    """Race equivalent + explicit constant-economy scaling, not VO2max=VDOT."""
    results = []
    for target, label in DISTANCES:
        ratio = target / distance
        # Include a wider endurance assumption for marathon extrapolation.
        powers = (1.06, 1.12 if max(target, distance) > 30 else 1.10)
        equivalents = [seconds * ratio ** power for power in powers]
        current = seconds * ratio ** mean(powers)
        low = min(equivalents) * current_vo2 / end['high']
        high = max(equivalents) * current_vo2 / end['low']
        results.append({'distance': target, 'label': label,
                        'currentSeconds': round(current),
                        'seconds': round(current * current_vo2 / end['vo2max']),
                        'lowSeconds': round(low), 'highSeconds': round(high)})
    return results


def potential(activities, metrics, health, today, weekly_km=None, weeks=12,
              assumed_gain=None, race_distance=None, race_seconds=None):
    start = today - timedelta(days=365)
    runs, activity_days = [], []
    for row in activities:
        d = day(row.get('date'))
        if d is None or not start <= d < today:
            continue
        activity_days.append(d)
        distance = number(row.get('distance'))
        if row.get('type') in RUN_TYPES and distance is not None and 0 < distance <= 500000:
            runs.append((d, distance / 1000))
    vo2 = {}
    for row in metrics:
        d, v = day(row.get('date')), number(row.get('vo2max'))
        if d is not None and start <= d <= today and v is not None and 10 <= v <= 100:
            vo2[d] = v
    latest = max(vo2) if vo2 else None
    current = median(v for d, v in vo2.items() if (latest - d).days < 7) if latest else None
    baseline = sum(km for d, km in runs if d >= today - timedelta(days=28)) / 4
    target = round(baseline) if weekly_km is None else weekly_km
    first = min(activity_days) if activity_days else today
    points = []
    # Complete calendar weeks only. Never forward-fill missing VO2 readings.
    monday = today - timedelta(days=today.weekday())
    for offset in range(51, -1, -1):
        end = monday - timedelta(weeks=offset)
        samples = [v for d, v in vo2.items() if end - timedelta(days=7) <= d < end]
        if not samples or first > end - timedelta(days=28):
            continue
        km = sum(km for d, km in runs if end - timedelta(days=28) <= d < end) / 4
        points.append({'date': (end - timedelta(days=1)).isoformat(),
                       'km': round(km, 2), 'vo2max': round(median(samples), 2)})
    model = _model(points)
    warnings = []
    fresh = latest is not None and (today - latest).days <= 14
    enough_volume = first <= today - timedelta(days=28) and baseline >= 5
    if not fresh:
        warnings.append('Ett VO₂max-värde från de senaste 14 dagarna behövs för att räkna framåt.')
    if not enough_volume:
        warnings.append('Minst fyra veckors aktivitetshistorik och 5 km/vecka i snitt behövs för en mängdbaserad modell.')
    if target > baseline * 1.3 and baseline > 0:
        warnings.append('Vald mängd är mer än 30 % över ditt senaste snitt. Grafen räknar inte på skaderisk eller tiden att bygga upp mängden; detta är inget träningsråd.')
    mode = 'history' if model['available'] and enough_volume and assumed_gain is None else 'hypothesis'
    if mode == 'history' and not model['minKm'] <= target <= model['maxKm']:
        warnings.append('Vald mängd ligger utanför den historik modellen har prövats på. Välj ett eget antagande för att utforska den mängden.')
        mode = 'unavailable'
    gain = 0 if assumed_gain is None else assumed_gain
    projection = []
    if fresh and mode != 'unavailable':
        # Six-week relaxation is a display assumption, not a learned adaptation
        # constant. Hypothesis gain is defined at 12 weeks, not as an annual rate.
        delta = (model['slope'] * (math.log1p(target) - math.log1p(baseline))
                 if mode == 'history' else current * gain / 100 / (1 - math.exp(-2)))
        # Do not silently cap an unsupported regression into a believable number.
        if mode == 'history' and abs(delta) > current * .15:
            mode = 'unavailable'
            warnings.append('Modellen ger för stor förändring för att visas som ett historikbaserat scenario.')
        else:
            for week in range(weeks + 1):
                fraction = 1 - math.exp(-week / 6)
                value = current + delta * fraction
                # Sensitivity band, explicitly NOT a confidence interval. A
                # floor prevents a deceptively narrow band from four test weeks.
                spread = max(1.5, (model['mae'] or 0) * 2) * math.sqrt(week / 12)
                projection.append({'week': week, 'vo2max': round(value, 2),
                                   'low': round(max(10, value - spread), 2),
                                   'high': round(value + spread, 2)})
    recent_health = [r for r in health if day(r.get('date')) is not None
                     and today - timedelta(days=28) <= day(r['date']) < today]
    recovery = {}
    for field in ('sleep_hours', 'hrv_avg'):
        values = [number(r.get(field)) for r in recent_health]
        values = [v for v in values if v is not None and v > 0]
        recovery[field] = round(mean(values), 1) if len(values) >= 5 else None
    races = race_scenarios(race_distance, race_seconds, current, projection[-1]) if (
        race_distance and race_seconds and projection) else []
    return {'currentVo2max': current, 'vo2Date': latest.isoformat() if latest else None,
            'baselineKm': round(baseline, 1), 'weeklyKm': target, 'weeks': weeks,
            'mode': mode if fresh else 'unavailable', 'assumedGain': gain,
            'model': model, 'history': points, 'projection': projection,
            'recovery': recovery, 'warnings': warnings, 'races': races,
            'runningActivities': len(runs), 'generatedDate': today.isoformat()}
