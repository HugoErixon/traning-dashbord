"""Pure summaries of Garmin intensity and training load; no IO or AI.

Missing days have zero load. CTL/ATL start at zero 90 days before the output
window and use alpha = 1 - exp(-1/tau), with tau = 42/7 days. TSB is yesterday's
CTL minus yesterday's ATL (the TrainingPeaks start-of-day convention).
Garmin activityTrainingLoad is the input, not cycling TSS.
"""
from collections import defaultdict
from datetime import date, timedelta
import math

import plan_calendar
import vdot as vdot_model


ASSUMPTIONS = [
    'Z1–3 räknas som låg intensitet, Z4 som tröskel och Z5 som hög intensitet, '
    'från Garmins hrTimeInZone_1..5 i sekunder. Löpning samt alla andra aktiviteter '
    'med pulszontid ingår. Andelarna räknas bara på tid med zondata; saknade '
    'zoner antas inte vara låg intensitet. totalMin är aktiviteternas totala tid '
    '(zontid om duration saknas), runKm gäller bara löpning.',
    'Belastning = Garmins activityTrainingLoad för alla aktiviteter, inte TSS. '
    'Dagar utan registrerad belastning räknas som noll. CTL/ATL är EWMA med '
    'tidskonstanter 42/7 dagar och 90 dagars uppvärmning från noll före fönstret. '
    'TSB använder gårdagens CTL minus ATL (TrainingPeaks-konvention); ramp är '
    'dagens CTL minus CTL för sju dagar sedan. Tolkningen är en tumregel.',
]


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def weekly_intensity(activities, weeks, today):
    """Oldest first, including the current partial week and empty weeks.

    Session counts/total duration include runs without zones, but their time is
    excluded from the intensity-share denominator. Future activities are ignored.
    """
    current = plan_calendar.plan_week(today)
    buckets = {}
    for week in range(current - weeks + 1, current + 1):
        buckets[week] = dict(week=week, isoWeek=plan_calendar.iso_week_label(week),
                             monday=plan_calendar.plan_monday(week).isoformat(),
                             lowMin=0.0, thresholdMin=0.0, highMin=0.0, totalMin=0.0,
                             runKm=0.0, sessions=0, hasZones=False)
    for activity in activities or []:
        day = _day(activity.get('date'))
        if day is None or day > today:
            continue
        bucket = buckets.get(plan_calendar.plan_week(day))
        if bucket is None:
            continue
        raw = activity.get('raw') or {}
        running = 'run' in str((raw.get('activityType') or {}).get('typeKey', '')).lower()
        zones = [_number(raw.get(f'hrTimeInZone_{i}')) / 60 for i in range(1, 6)]
        zone_minutes = sum(zones)
        if not running and not zone_minutes:
            continue
        bucket['sessions'] += 1
        bucket['lowMin'] += sum(zones[:3])
        bucket['thresholdMin'] += zones[3]
        bucket['highMin'] += zones[4]
        bucket['totalMin'] += _number(raw.get('duration')) / 60 or zone_minutes
        bucket['hasZones'] |= zone_minutes > 0
        if running:
            bucket['runKm'] += _number(raw.get('distance')) / 1000
    for bucket in buckets.values():
        zoned = bucket['lowMin'] + bucket['thresholdMin'] + bucket['highMin']
        for name in ('low', 'threshold', 'high'):
            bucket[name + 'Share'] = round(bucket[name + 'Min'] / zoned, 4) if zoned else 0.0
        for name in ('lowMin', 'thresholdMin', 'highMin', 'totalMin', 'runKm'):
            bucket[name] = round(bucket[name], 2)
    return list(buckets.values())


def _interpretation(tsb, ramp):
    if tsb < -30:
        fatigue = 'Hög trötthetsrisk.'
    elif tsb < -10:
        fatigue = 'Produktiv belastning.'
    elif tsb <= 5:
        fatigue = 'Neutral balans mellan belastning och återhämtning.'
    else:
        fatigue = 'Utvilad/formtopp.'
    if ramp > 8:
        build = 'Varning: belastningen ökar mer än 8 CTL per vecka.'
    elif ramp >= 3:
        build = 'Kraftig men hanterbar uppbyggnad.'
    elif ramp < 0:
        build = 'Nedtrappning/avlastning.'
    else:
        build = 'Stabil eller försiktig uppbyggnad.'
    return fatigue + ' ' + build


def load_series(activities, days, today):
    """Return {'series': daily rows, 'summary': today's values and interpretation}."""
    if days < 1:
        raise ValueError('days must be positive')
    first = today - timedelta(days=days - 1)
    start = first - timedelta(days=90)
    loads = defaultdict(float)
    for activity in activities or []:
        day = _day(activity.get('date'))
        if day and start <= day <= today:
            loads[day] += _number((activity.get('raw') or {}).get('activityTrainingLoad'))
    ctl = atl = 0.0
    alpha_ctl, alpha_atl = 1 - math.exp(-1 / 42), 1 - math.exp(-1 / 7)
    history, series = {}, []
    day = start
    while day <= today:
        tsb = ctl - atl
        load = loads[day]
        ctl += alpha_ctl * (load - ctl)
        atl += alpha_atl * (load - atl)
        history[day] = ctl
        if day >= first:
            series.append(dict(date=day.isoformat(), load=round(load, 2),
                               ctl=round(ctl, 2), atl=round(atl, 2), tsb=round(tsb, 2)))
        day += timedelta(days=1)
    ramp = ctl - history[today - timedelta(days=7)]
    summary = {key: series[-1][key] for key in ('ctl', 'atl', 'tsb')}
    summary.update(rampPerWeek=round(ramp, 2), interpretation=_interpretation(tsb, ramp))
    return {'series': series, 'summary': summary}


def race_equivalents(vdot):
    """Daniels equivalent performances, with pace in seconds/km and readable text."""
    result = []
    for label, km in (('1500 m', 1.5), ('3 km', 3), ('5 km', 5), ('10 km', 10),
                      ('Halvmaraton', vdot_model.HALF_KM), ('Maraton', vdot_model.MARATHON_KM)):
        seconds = vdot_model.race_time(vdot, km)
        if seconds is None:
            return []
        hours, rest = divmod(seconds, 3600)
        minutes, sec = divmod(rest, 60)
        text = f'{hours}:{minutes:02d}:{sec:02d}' if hours else f'{minutes}:{sec:02d}'
        pace = round(seconds / km)
        result.append(dict(label=label, km=km, seconds=seconds, text=text,
                           paceSec=pace, pace=f'{pace // 60}:{pace % 60:02d}/km'))
    return result
