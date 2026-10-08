"""Long-term body and fitness trends for the overview page.

The analysis page answers "is the training working?"; this module answers the
simpler question the start page asks: how are VO2max, HRV, resting pulse and
sleep moving over weeks and months?  Daily markers are noisy night to night, so
they are compared as 7-day averages; slow markers (VO2max, endurance, threshold
pace) are already smoothed by Garmin and are compared value to value.

Plain dictionaries in, plain dictionaries out — testable without Garmin or
PostgreSQL.
"""

from datetime import date, timedelta


SLEEP_GOAL_HOURS = 7.5
# Gränsen för "kort natt" i sömn/HRV-jämförelsen. Lägre än sömnmålet med flit:
# en natt på 7,3 h är inte kort, och skillnaden syns tydligare med en ärlig gräns.
SHORT_NIGHT_HOURS = 7.0
MIN_NIGHTS_PER_GROUP = 5
# Ett 7-dagarssnitt på färre nätter än så är en enskild natt i förklädnad.
MIN_SAMPLES_FOR_AVERAGE = 3

# key, label, unit, fmt, good, source, field, noise, kind
#   noise: minsta förändring som räknas som verklig och inte mätbrus
#   kind:  'daily' jämförs som 7-dagarssnitt, 'level' som enskilda värden
TREND_SPECS = (
    ('vo2max', 'VO₂max', '', 1, 'up', 'metric', 'vo2max', 0.5, 'level'),
    ('hrv', 'HRV', 'ms', 0, 'up', 'health', 'hrv_avg', 2.0, 'daily'),
    ('rhr', 'Vilopuls', 'slag/min', 0, 'down', 'health', 'resting_hr', 1.0, 'daily'),
    ('sleep_score', 'Sömnpoäng', '', 0, 'up', 'health', 'sleep_score', 3.0, 'daily'),
    ('sleep_hours', 'Sömntid', 'h', 1, 'up', 'health', 'sleep_hours', 0.25, 'daily'),
    ('endurance', 'Uthållighet', '', 0, 'up', 'metric', 'endurance_score', 50.0, 'level'),
    # Tröskelfarten lagras dagligen men Garmin växlar ofta mellan två värden
    # dag för dag (4:00/4:08), så den jämförs som snitt och inte värde mot värde.
    ('lt_pace', 'Tröskelfart', '/km', 'pace', 'down', 'metric', 'lactate_pace', 3.0, 'daily'),
    ('body_battery', 'Body Battery', '', 0, 'up', 'health', 'body_battery', 5.0, 'daily'),
    ('stress', 'Stress', '', 0, 'down', 'health', 'stress_avg', 3.0, 'daily'),
)
# Dagens stressnivå är ett snitt över en dag som inte är slut än.
PARTIAL_TODAY = {'stress'}


def _as_date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _number(value):
    try:
        number = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return number


def _mean(values):
    return sum(values) / len(values) if values else None


def _clean_series(rows, field, start, today, skip_today=False):
    by_day = {}
    for row in rows or []:
        day = _as_date(row.get('date'))
        value = _number(row.get(field))
        if day is None or value is None or day > today or day < start:
            continue
        if skip_today and day == today:
            continue
        # Garmin rapporterar 0 när mätningen saknas (t.ex. HRV utan klocka i natt).
        if value <= 0:
            continue
        by_day[day] = value
    return sorted(by_day.items())


def _window_mean(points, first_day, last_day):
    values = [v for d, v in points if first_day <= d <= last_day]
    if len(values) < MIN_SAMPLES_FOR_AVERAGE:
        return None, len(values)
    return _mean(values), len(values)


def _rolling(points):
    output = []
    for index, (day, _) in enumerate(points):
        window = [v for d, v in points[:index + 1] if (day - d).days < 7]
        if len(window) >= MIN_SAMPLES_FOR_AVERAGE:
            output.append({'t': day.isoformat(), 'v': round(_mean(window), 2)})
    return output


def _direction(delta, noise, good):
    if delta is None:
        return 'unknown'
    if abs(delta) < noise:
        return 'stable'
    favourable = (delta > 0 and good == 'up') or (delta < 0 and good == 'down')
    return 'improving' if favourable else 'declining'


def trend(spec, health_rows, metric_rows, today, window_days):
    key, label, unit, fmt, good, source, field, noise, kind = spec
    rows = health_rows if source == 'health' else metric_rows
    start = today - timedelta(days=window_days)
    # Extra historik bakåt så att periodens startnivå kan mätas även om de
    # första dagarna saknar data. VO2max och de andra långsamma markörerna
    # uppdateras glest, så de får titta längre bakåt än en vecka.
    lookback = 7 if kind == 'daily' else 30
    points = _clean_series(rows, field, start - timedelta(days=lookback), today,
                           skip_today=key in PARTIAL_TODAY)
    in_window = [(d, v) for d, v in points if d >= start]
    result = {
        'key': key, 'label': label, 'unit': unit, 'fmt': fmt, 'good': good,
        'kind': kind, 'noise': noise,
        'series': [{'t': d.isoformat(), 'v': v} for d, v in in_window],
        'smooth': [], 'latest': None, 'latestDate': None,
        'now': None, 'then': None, 'thenDate': None, 'delta': None,
        'min': None, 'max': None, 'baseline28': None,
        'direction': 'unknown', 'samples': len(in_window),
    }
    if not in_window:
        return result

    values = [v for _, v in in_window]
    result['latest'] = in_window[-1][1]
    result['latestDate'] = in_window[-1][0].isoformat()
    result['min'] = min(values)
    result['max'] = max(values)

    if kind == 'daily':
        result['smooth'] = [p for p in _rolling(points) if _as_date(p['t']) >= start]
        last_day = in_window[-1][0]
        now, _ = _window_mean(points, last_day - timedelta(days=6), last_day)
        # Startnivån mäts från periodens första vecka med data — en lång period
        # som börjar före historiken ska jämföras med där historiken börjar.
        anchor = start if points[0][0] <= start else in_window[0][0]
        then, _ = _window_mean(points, anchor, anchor + timedelta(days=6))
        if anchor + timedelta(days=6) >= last_day - timedelta(days=6):
            then = None  # start- och slutveckan överlappar: ingen förändring att mäta
        baseline, _ = _window_mean(points, today - timedelta(days=27), today)
        result['now'] = round(now, 2) if now is not None else None
        result['then'] = round(then, 2) if then is not None else None
        result['thenDate'] = anchor.isoformat() if then is not None else None
        result['baseline28'] = round(baseline, 2) if baseline is not None else None
    else:
        result['smooth'] = list(result['series'])
        result['now'] = result['latest']
        before = [(d, v) for d, v in points if d <= start]
        first = before[-1] if before else in_window[0]
        # Ett enda mätvärde i hela perioden är ingen förändring.
        if first[0] != in_window[-1][0]:
            result['then'] = first[1]
            result['thenDate'] = first[0].isoformat()

    if result['now'] is not None and result['then'] is not None:
        result['delta'] = round(result['now'] - result['then'], 2)
    result['direction'] = _direction(result['delta'], noise, good)
    return result


def weekly_sleep(health_rows, today, window_days, goal=SLEEP_GOAL_HOURS):
    """Average sleep per ISO week, oldest first."""
    start = today - timedelta(days=window_days)
    hours = dict(_clean_series(health_rows, 'sleep_hours', start, today))
    scores = dict(_clean_series(health_rows, 'sleep_score', start, today))
    weeks = {}
    for day in sorted(set(hours) | set(scores)):
        monday = day - timedelta(days=day.weekday())
        bucket = weeks.setdefault(monday, {'hours': [], 'scores': []})
        if day in hours:
            bucket['hours'].append(hours[day])
        if day in scores:
            bucket['scores'].append(scores[day])
    output = []
    current_monday = today - timedelta(days=today.weekday())
    for monday in sorted(weeks):
        bucket = weeks[monday]
        avg_hours = _mean(bucket['hours'])
        avg_score = _mean(bucket['scores'])
        output.append({
            'week': monday.isocalendar()[1],
            'start': monday.isoformat(),
            'nights': max(len(bucket['hours']), len(bucket['scores'])),
            'avgHours': round(avg_hours, 2) if avg_hours is not None else None,
            'avgScore': round(avg_score) if avg_score is not None else None,
            'nightsOnGoal': sum(1 for h in bucket['hours'] if h >= goal),
            'current': monday == current_monday,
        })
    return output


def sleep_vs_recovery(health_rows, today, days=120, threshold=SHORT_NIGHT_HOURS):
    """HRV and resting pulse after short versus normal nights.

    Same row on purpose: health_history stores the HRV and resting pulse
    measured during the night that ended on that date.  This is an
    association in the athlete's own data, not proof of cause.
    """
    start = today - timedelta(days=days)
    groups = {'short': {'hrv': [], 'rhr': []}, 'normal': {'hrv': [], 'rhr': []}}
    for row in health_rows or []:
        day = _as_date(row.get('date'))
        hours = _number(row.get('sleep_hours'))
        if day is None or day < start or day > today or not hours or hours <= 0:
            continue
        group = groups['short' if hours < threshold else 'normal']
        hrv = _number(row.get('hrv_avg'))
        rhr = _number(row.get('resting_hr'))
        if hrv and hrv > 0:
            group['hrv'].append(hrv)
        if rhr and rhr > 0:
            group['rhr'].append(rhr)

    short_n = len(groups['short']['hrv'])
    normal_n = len(groups['normal']['hrv'])
    result = {
        'thresholdHours': threshold, 'days': days,
        'shortNights': short_n, 'normalNights': normal_n,
        'enough': short_n >= MIN_NIGHTS_PER_GROUP and normal_n >= MIN_NIGHTS_PER_GROUP,
        'hrvShort': None, 'hrvNormal': None, 'hrvDiff': None,
        'rhrShort': None, 'rhrNormal': None, 'rhrDiff': None,
    }
    if not result['enough']:
        return result
    for metric in ('hrv', 'rhr'):
        short = _mean(groups['short'][metric])
        normal = _mean(groups['normal'][metric])
        if short is None or normal is None:
            continue
        result[f'{metric}Short'] = round(short, 1)
        result[f'{metric}Normal'] = round(normal, 1)
        result[f'{metric}Diff'] = round(short - normal, 1)
    return result


def overview(health_rows, metric_rows, today, window_days):
    trends = [trend(spec, health_rows, metric_rows, today, window_days)
              for spec in TREND_SPECS]
    improving = [t['key'] for t in trends if t['direction'] == 'improving']
    declining = [t['key'] for t in trends if t['direction'] == 'declining']
    return {
        'windowDays': window_days,
        'today': today.isoformat(),
        'trends': trends,
        'summary': {
            'improving': improving,
            'declining': declining,
            'stable': [t['key'] for t in trends if t['direction'] == 'stable'],
        },
        'sleepWeeks': weekly_sleep(health_rows, today, window_days),
        'sleepGoalHours': SLEEP_GOAL_HOURS,
        'sleepVsRecovery': sleep_vs_recovery(health_rows, today),
    }
