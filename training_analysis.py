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


TREND_BASE = 60
TREND_MIN = 20
TREND_MAX = 95
TREND_PER_IMPROVING = 7
TREND_PER_DECLINING = 8


def overview(metrics, volume, execution, goal=None):
    """One honest page-level verdict and the most useful next actions.

    The score is assembled and explained in the same pass, so `breakdown`
    can never drift away from the number it claims to describe: the entries
    add up to `rawScore`, and `score` is that value clamped.
    """
    signal_keys = {'hrv', 'rhr', 'sleep', 'vo2max', 'endurance', 'lt_pace'}
    signals = [m for m in metrics if m['key'] in signal_keys and m['samples'] >= 3]
    improving = [m for m in signals if m['direction'] == 'improving']
    declining = [m for m in signals if m['direction'] == 'declining']

    breakdown = [{
        'key': 'base', 'label': 'Utgångsläge', 'delta': None, 'tone': 'neutral',
        'detail': f'Varje trendpoäng börjar på {TREND_BASE} och rör sig därifrån.',
    }]
    score = TREND_BASE

    if improving:
        delta = len(improving) * TREND_PER_IMPROVING
        score += delta
        breakdown.append({
            'key': 'improving', 'delta': delta, 'tone': 'good',
            'label': f'{len(improving)} markör{"er" if len(improving) > 1 else ""} förbättras',
            'detail': ', '.join(m['label'] for m in improving)
                      + f' · +{TREND_PER_IMPROVING} per markör.',
        })
    if declining:
        delta = -len(declining) * TREND_PER_DECLINING
        score += delta
        breakdown.append({
            'key': 'declining', 'delta': delta, 'tone': 'warn',
            'label': f'{len(declining)} markör{"er" if len(declining) > 1 else ""} försämras',
            'detail': ', '.join(m['label'] for m in declining)
                      + f' · −{TREND_PER_DECLINING} per markör.',
        })
    stable = [m for m in signals if m not in improving and m not in declining]
    if stable:
        breakdown.append({
            'key': 'stable', 'delta': 0, 'tone': 'neutral',
            'label': f'{len(stable)} markör{"er" if len(stable) > 1 else ""} ligger stilla',
            'detail': ', '.join(m['label'] for m in stable)
                      + ' · rör sig inte mer än mätbruset och påverkar därför inte poängen.',
        })

    adherence = execution.get('adherencePct')
    if adherence is None:
        breakdown.append({
            'key': 'adherence', 'delta': None, 'tone': 'neutral',
            'label': 'Planföljsamhet saknas',
            'detail': 'Inga avgjorda planpass i perioden, så följsamheten påverkar inte poängen.',
        })
    else:
        delta = 8 if adherence >= 85 else (-8 if adherence < 65 else 0)
        score += delta
        if delta > 0:
            detail = f'{adherence}% av planpassen genomförda — 85% eller mer ger +8.'
        elif delta < 0:
            detail = f'{adherence}% av planpassen genomförda — under 65% ger −8.'
        else:
            detail = f'{adherence}% av planpassen genomförda — mellan 65% och 85% ger varken plus eller minus.'
        breakdown.append({
            'key': 'adherence', 'delta': delta, 'label': 'Planföljsamhet', 'detail': detail,
            'tone': 'good' if delta > 0 else ('warn' if delta < 0 else 'neutral'),
        })

    quality = execution.get('qualityPct')
    if quality is None:
        breakdown.append({
            'key': 'quality', 'delta': None, 'tone': 'neutral',
            'label': 'Passkvalitet saknas',
            'detail': 'Inga pass med utförandedata ännu, så kvaliteten påverkar inte poängen.',
        })
    else:
        delta = 5 if quality >= 75 else (-5 if quality < 50 else 0)
        score += delta
        if delta > 0:
            detail = f'{quality}% av passen låg på rätt nivå — 75% eller mer ger +5.'
        elif delta < 0:
            detail = f'{quality}% av passen låg på rätt nivå — under 50% ger −5.'
        else:
            detail = f'{quality}% av passen låg på rätt nivå — mellan 50% och 75% ger varken plus eller minus.'
        breakdown.append({
            'key': 'quality', 'delta': delta, 'label': 'Pass på rätt nivå', 'detail': detail,
            'tone': 'good' if delta > 0 else ('warn' if delta < 0 else 'neutral'),
        })

    raw_score = round(score)
    score = max(TREND_MIN, min(TREND_MAX, raw_score))
    if score != raw_score:
        breakdown.append({
            'key': 'clamp', 'delta': score - raw_score, 'tone': 'neutral',
            'label': f'Begränsad till {score}',
            'detail': f'Summan blev {raw_score}; skalan går bara mellan '
                      f'{TREND_MIN} och {TREND_MAX}.',
        })

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
    if len(signals) < 2:
        confidence_note = ('Färre än två markörer har tillräckligt med mätpunkter, '
                           'så poängen vilar mest på utgångsläget.')
    else:
        confidence_note = (f'{len(signals)} av {len(signal_keys)} trendmarkörer har minst tre '
                           f'mätpunkter, och {execution.get("evaluated", 0)} pass är utvärderade.')

    return {
        'score': score, 'status': status, 'title': title,
        'improving': len(improving), 'declining': len(declining),
        'signals': len(signals), 'confidencePct': confidence,
        'priorities': priorities[:3],
        'rawScore': raw_score, 'base': TREND_BASE,
        'scale': {'min': TREND_MIN, 'max': TREND_MAX},
        'breakdown': breakdown, 'confidenceNote': confidence_note,
    }
