"""Deterministic derivations for the training analysis page.

The API owns the interpretation so the browser only has to present it.  All
functions take plain dictionaries and can therefore be tested without Garmin
or PostgreSQL.
"""

from datetime import date, timedelta


RUN_TYPES = {'running', 'track_running', 'treadmill_running', 'trail_running'}

METRIC_SPECS = (
    ('hrv', 'HRV', 'ms', 0, 'up', 0.5),
    ('rhr', 'Vilopuls', 'slag/min', 0, 'down', 0.3),
    ('sleep', 'Sömnpoäng', '', 0, 'up', 0.5),
    ('vo2max', 'VO₂max', '', 1, 'up', 0.05),
    ('endurance', 'Uthållighet', '', 0, 'up', 5.0),
    ('lt_pace', 'Tröskelfart', 'tempo', 'pace', 'down', 0.5),
    ('lt_hr', 'Tröskelpuls', 'slag/min', 0, 'neutral', 0.3),
)

NEGATIVE_EXECUTION_FLAGS = {
    'reps_below_target_pace', 'reps_above_target_pace', 'faded_across_reps',
    'fewer_reps_than_planned', 'easy_run_too_fast', 'easy_run_hr_too_high',
    'slower_than_target', 'high_cardiac_drift', 'cut_session_short',
    'strength_too_light', 'missed_sets',
}


def _as_date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _slope_per_week(series):
    """Least-squares slope using real calendar distance between samples."""
    if len(series) < 2:
        return None
    origin = _as_date(series[0]['t'])
    points = [((_as_date(p['t']) - origin).days, p['v']) for p in series if _as_date(p['t'])]
    if len(points) < 2:
        return None
    n = len(points)
    sx = sum(x for x, _ in points)
    sy = sum(y for _, y in points)
    sxx = sum(x * x for x, _ in points)
    sxy = sum(x * y for x, y in points)
    denominator = n * sxx - sx * sx
    if not denominator:
        return 0.0
    return ((n * sxy - sx * sy) / denominator) * 7


def metric(key, label, unit, fmt, good, threshold, series):
    """Describe one time series, including a noise-aware direction."""
    clean = []
    for point in series or []:
        day = _as_date(point.get('t'))
        value = _number(point.get('v'))
        if day and value is not None:
            clean.append({'t': day.isoformat(), 'v': value})
    clean.sort(key=lambda point: point['t'])
    result = {
        'key': key, 'label': label, 'unit': unit, 'fmt': fmt, 'good': good,
        'series': clean, 'latest': None, 'first': None, 'slopePerWeek': None,
        'pctChange': None, 'direction': 'unknown', 'samples': len(clean),
    }
    if not clean:
        return result

    result['latest'] = clean[-1]['v']
    result['first'] = clean[0]['v']
    slope = _slope_per_week(clean)
    result['slopePerWeek'] = round(slope, 3) if slope is not None else None
    if clean[0]['v']:
        result['pctChange'] = round((clean[-1]['v'] - clean[0]['v']) / abs(clean[0]['v']) * 100, 1)
    if len(clean) < 3 or slope is None or abs(slope) < threshold:
        result['direction'] = 'stable' if len(clean) >= 2 else 'unknown'
    elif good == 'neutral':
        result['direction'] = 'rising' if slope > 0 else 'falling'
    else:
        favourable = (slope > 0 and good == 'up') or (slope < 0 and good == 'down')
        result['direction'] = 'improving' if favourable else 'declining'
    return result


def build_metrics(health_rows, metric_rows, load_series):
    health_fields = {'hrv': 'hrv_avg', 'rhr': 'resting_hr', 'sleep': 'sleep_score'}
    metric_fields = {
        'vo2max': 'vo2max', 'endurance': 'endurance_score',
        'lt_pace': 'lactate_pace', 'lt_hr': 'lactate_hr',
    }
    output = []
    for key, label, unit, fmt, good, threshold in METRIC_SPECS:
        rows = health_rows if key in health_fields else metric_rows
        field = health_fields.get(key) or metric_fields.get(key)
        series = [{'t': row.get('date'), 'v': row.get(field)} for row in rows]
        output.append(metric(key, label, unit, fmt, good, threshold, series))
    output.append(metric('training_load', 'Belastning · 7 dygn', 'load', 'load',
                         'neutral', 2.0, load_series))
    return output


def weekly_volume(activities, today=None, weeks=8):
    """Calendar-week bars plus comparable rolling seven-day totals."""
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    buckets = []
    for offset in range(weeks - 1, -1, -1):
        start = monday - timedelta(weeks=offset)
        buckets.append({'start': start.isoformat(), 'km': 0.0, 'sessions': 0, 'load': 0.0})

    parsed = []
    for activity in activities or []:
        day = _as_date(activity.get('date'))
        raw = activity.get('raw') or {}
        type_key = activity.get('type') or (raw.get('activityType') or {}).get('typeKey', '')
        distance = _number(activity.get('distance'))
        if distance is None:
            distance = _number(raw.get('distance')) or 0
        load = _number(raw.get('activityTrainingLoad')) or 0
        if day:
            parsed.append((day, type_key, distance, load))
        if not day or type_key not in RUN_TYPES:
            continue
        week_start = day - timedelta(days=day.weekday())
        bucket = next((item for item in buckets if item['start'] == week_start.isoformat()), None)
        if bucket:
            bucket['km'] += distance / 1000
            bucket['sessions'] += 1
            bucket['load'] += load

    for bucket in buckets:
        bucket['km'] = round(bucket['km'], 1)
        bucket['load'] = round(bucket['load'])
        start = _as_date(bucket['start'])
        bucket['label'] = f"v{start.isocalendar().week}"

    def rolling(start, end):
        runs = [(d, dist) for d, typ, dist, _ in parsed if typ in RUN_TYPES and start <= d <= end]
        return round(sum(distance for _, distance in runs) / 1000, 1), len(runs)

    current_km, current_sessions = rolling(today - timedelta(days=6), today)
    previous_km, _ = rolling(today - timedelta(days=13), today - timedelta(days=7))
    completed = buckets[:-1][-4:]
    avg4 = round(sum(item['km'] for item in completed) / len(completed), 1) if completed else 0
    delta = round((current_km - previous_km) / previous_km * 100, 1) if previous_km else None
    return {
        'weeks': buckets, 'current7Km': current_km, 'current7Sessions': current_sessions,
        'previous7Km': previous_km, 'average4WeeksKm': avg4, 'delta7Pct': delta,
    }


def execution_summary(sessions):
    completed = [s for s in sessions or [] if s.get('status') == 'completed']
    missed = [s for s in sessions or [] if s.get('status') == 'missed']
    skipped = [s for s in sessions or [] if s.get('status') == 'skipped']
    decided = len(completed) + len(missed) + len(skipped)
    evaluated = [s.get('execution') or {} for s in completed if s.get('execution')]
    on_target = sum(
        1 for execution in evaluated
        if not (set(execution.get('flags') or []) & NEGATIVE_EXECUTION_FLAGS)
    )
    return {
        'plannedPast': decided,
        'completed': len(completed), 'missed': len(missed), 'skipped': len(skipped),
        'adherencePct': round(len(completed) / decided * 100) if decided else None,
        'evaluated': len(evaluated), 'onTarget': on_target,
        'qualityPct': round(on_target / len(evaluated) * 100) if evaluated else None,
    }


def overview(metrics, volume, execution, goal=None):
    """One honest page-level verdict and the most useful next actions."""
    signal_keys = {'hrv', 'rhr', 'sleep', 'vo2max', 'endurance', 'lt_pace'}
    signals = [m for m in metrics if m['key'] in signal_keys and m['samples'] >= 3]
    improving = [m for m in signals if m['direction'] == 'improving']
    declining = [m for m in signals if m['direction'] == 'declining']
    score = 60 + len(improving) * 7 - len(declining) * 8
    adherence = execution.get('adherencePct')
    if adherence is not None:
        score += 8 if adherence >= 85 else (-8 if adherence < 65 else 0)
    quality = execution.get('qualityPct')
    if quality is not None:
        score += 5 if quality >= 75 else (-5 if quality < 50 else 0)
    score = max(20, min(95, round(score)))

    if len(signals) < 2:
        status, title = 'collecting', 'Samlar en tydligare trendbild'
    elif score >= 72:
        status, title = 'building', 'Formen byggs åt rätt håll'
    elif score >= 52:
        status, title = 'steady', 'Stabil grund — nästa steg är kvalitet'
    else:
        status, title = 'attention', 'Några signaler behöver vändas'

    priorities = []
    feasibility = (goal or {}).get('feasibility') or {}
    if feasibility.get('verdict') == 'out_of_reach':
        priorities.append({'tone': 'warn', 'title': 'Stäng gapet till målfarten',
                           'detail': f"Nuvarande kapacitet är {feasibility.get('gapSec')} s/km från målfarten."})
    for item in declining[:2]:
        priorities.append({'tone': 'warn', 'title': f"Vänd trenden i {item['label'].lower()}",
                           'detail': 'Utvecklingen över perioden går åt fel håll; prioritera återhämtning och rätt dos kvalitet.'})
    if adherence is not None and adherence < 75:
        priorities.append({'tone': 'warn', 'title': 'Skydda kontinuiteten',
                           'detail': f"{adherence}% av avgjorda planpass är genomförda under perioden."})
    delta = volume.get('delta7Pct')
    if delta is not None and delta > 20:
        priorities.append({'tone': 'watch', 'title': 'Volymen har ökat snabbt',
                           'detail': f"Rullande sju dagar är {delta:.0f}% över föregående period."})
    if not priorities and improving:
        priorities.append({'tone': 'good', 'title': 'Fortsätt på samma spår',
                           'detail': f"{len(improving)} centrala markörer förbättras utan tydliga varningssignaler."})
    if not priorities:
        priorities.append({'tone': 'neutral', 'title': 'Bygg mer underlag',
                           'detail': 'Fortsätt synka och genomföra planen så blir trendbilden säkrare.'})

    confidence = min(100, round(len(signals) / len(signal_keys) * 70
                                + min(execution.get('evaluated', 0), 6) / 6 * 30))
    return {
        'score': score, 'status': status, 'title': title,
        'improving': len(improving), 'declining': len(declining),
        'signals': len(signals), 'confidencePct': confidence,
        'priorities': priorities[:3],
    }
