"""Derived sleep metrics for the sleep page.

`health_history` stores one row per night. On its own that is a list of
numbers; what makes it useful is the shape across nights — how much sleep is
owed, whether bedtimes drift, and whether the stage split is where it should
be. Those derivations live here so they can be tested without a database.

All functions take plain dicts with the keys health_history uses:
date, sleep_score, sleep_hours, deep_pct, rem_pct, sleep_start, sleep_end.
"""

from statistics import pstdev

# The nightly target the rest of the app already assumes.
DEFAULT_TARGET_H = 7.5

# Stage targets, as shares of total sleep. Below the low end is where the
# recovery cost actually shows up.
DEEP_RANGE = (15, 25)
REM_RANGE = (20, 25)

# Bedtimes are compared as minutes after 18:00 so that 23:40 and 00:20 read as
# forty minutes apart rather than twenty-three hours.
_BEDTIME_ORIGIN_H = 18

# Only 18:00–06:00 counts as going to bed for the night. Garmin sometimes
# records a long afternoon nap as the day's main sleep, and letting that
# through would make an otherwise regular schedule look erratic.
_BEDTIME_WINDOW_MIN = 12 * 60


def _hhmm_to_minutes(value):
    """Accept 'HH:MM', ISO timestamps or minute counts; return minutes or None."""
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value)
    if 'T' in text:
        text = text.split('T', 1)[1]
    elif ' ' in text and ':' in text:
        text = text.split(' ', 1)[1]
    parts = text.strip().split(':')
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return None


def _bedtime_offset(minutes):
    """Minutes after 18:00, wrapping past midnight."""
    if minutes is None:
        return None
    offset = minutes - _BEDTIME_ORIGIN_H * 60
    return offset + 1440 if offset < 0 else offset


def format_hours(hours):
    """2.75 -> '2 h 45 min'."""
    if hours is None:
        return None
    total = int(round(float(hours) * 60))
    h, m = divmod(abs(total), 60)
    sign = '-' if total < 0 else ''
    return f"{sign}{h} h {m:02d} min" if h else f"{sign}{m} min"


def format_clock(minutes):
    """Minutes past midnight -> 'HH:MM'."""
    if minutes is None:
        return None
    minutes = int(minutes) % 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def sleep_debt(nights, target_h=DEFAULT_TARGET_H, days=7):
    """How much sleep the last week owes against the nightly target.

    Only nights with recorded hours count, so a missing night neither adds
    nor forgives debt.
    """
    recent = [n for n in nights[:days] if n.get('sleep_hours') is not None]
    if not recent:
        return None
    slept = sum(float(n['sleep_hours']) for n in recent)
    target = target_h * len(recent)
    return {
        'nights': len(recent),
        'sleptH': round(slept, 2),
        'targetH': round(target, 2),
        'debtH': round(max(0.0, target - slept), 2),
        'surplusH': round(max(0.0, slept - target), 2),
        'averageH': round(slept / len(recent), 2),
    }


def bedtime_consistency(nights, days=14):
    """How much bedtimes drift night to night.

    Irregular timing costs recovery even when the total is fine, so this is
    reported on its own rather than folded into the sleep score.
    """
    offsets = []
    naps = 0
    for night in nights[:days]:
        offset = _bedtime_offset(_hhmm_to_minutes(night.get('sleep_start')))
        if offset is None:
            continue
        if offset > _BEDTIME_WINDOW_MIN:
            naps += 1  # daytime sleep, not a bedtime
            continue
        offsets.append(offset)
    if len(offsets) < 3:
        return None

    spread = pstdev(offsets)
    average = sum(offsets) / len(offsets)
    if spread <= 30:
        verdict = 'steady'
    elif spread <= 60:
        verdict = 'drifting'
    else:
        verdict = 'irregular'
    return {
        'nights': len(offsets),
        'napsExcluded': naps,
        'spreadMin': round(spread),
        'averageBedtime': format_clock(average + _BEDTIME_ORIGIN_H * 60),
        'earliest': format_clock(min(offsets) + _BEDTIME_ORIGIN_H * 60),
        'latest': format_clock(max(offsets) + _BEDTIME_ORIGIN_H * 60),
        'verdict': verdict,
    }


def stage_balance(deep_pct, rem_pct):
    """Judge the deep/REM split against the ranges the app already uses."""
    result = {'deepPct': deep_pct, 'remPct': rem_pct, 'flags': []}
    if deep_pct is not None:
        if deep_pct < DEEP_RANGE[0]:
            result['flags'].append('deep_low')
        elif deep_pct > DEEP_RANGE[1]:
            result['flags'].append('deep_high')
    if rem_pct is not None:
        if rem_pct < REM_RANGE[0]:
            result['flags'].append('rem_low')
        elif rem_pct > REM_RANGE[1]:
            result['flags'].append('rem_high')
    return result


def trend(nights, field, days=14):
    """Direction and rate of change per week, by least squares.

    Mirrors how the analysis tab already reports trends, so the two pages
    cannot disagree about which way a number is moving.
    """
    points = []
    for index, night in enumerate(nights[:days]):
        value = night.get(field)
        if value is not None:
            points.append((index, float(value)))
    if len(points) < 4:
        return None

    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if not denominator:
        return None
    # Index 0 is the most recent night, so a positive slope here means the
    # value was higher further back — flip it to read as "change over time".
    slope_per_day = -sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator

    per_week = slope_per_day * 7
    if abs(per_week) < 0.05 * max(abs(mean_y), 1):
        direction = 'stable'
    else:
        direction = 'improving' if per_week > 0 else 'declining'
    return {'field': field, 'nights': n, 'perWeek': round(per_week, 2),
            'average': round(mean_y, 2), 'direction': direction}


def goal_streak(nights, target_h=DEFAULT_TARGET_H):
    """Consecutive most-recent nights that met the target."""
    streak = 0
    for night in nights:
        hours = night.get('sleep_hours')
        if hours is None or float(hours) < target_h:
            break
        streak += 1
    return streak


def summarize(nights, target_h=DEFAULT_TARGET_H):
    """Everything the sleep page needs derived from the nightly history."""
    nights = [n for n in (nights or []) if n]
    scored = [n for n in nights if n.get('sleep_score') is not None]
    best = max(scored, key=lambda n: n['sleep_score'], default=None)
    worst = min(scored, key=lambda n: n['sleep_score'], default=None)

    return {
        'debt': sleep_debt(nights, target_h),
        'consistency': bedtime_consistency(nights),
        'streak': goal_streak(nights, target_h),
        'trends': {
            field: trend(nights, field)
            for field in ('sleep_score', 'sleep_hours', 'deep_pct', 'rem_pct')
        },
        'best': {'date': best['date'], 'score': best['sleep_score']} if best else None,
        'worst': {'date': worst['date'], 'score': worst['sleep_score']} if worst else None,
        'targetH': target_h,
    }
