"""Daily strain scoring and post-session verdicts.

Strain answers one question: what did today cost, relative to what this body
normally absorbs.  A raw Garmin training load of 180 means nothing on its own —
it is a hard day for one athlete and a Tuesday for another — so every load here
is expressed against the athlete's own chronic load.

The session verdict turns a finished activity into the two decisions that
actually follow it: how to recover tonight, and when the next quality session
can realistically go.

All functions take plain dictionaries and can therefore be tested without
Garmin or PostgreSQL.
"""

from datetime import date, timedelta

from training_analysis import RUN_TYPES, _as_date, _number


# Fallback daily load when the athlete has no chronic history yet.  Roughly one
# easy hour; deliberately conservative so a first session does not read as 100.
DEFAULT_REFERENCE_LOAD = 60.0

# Ratio of daily load to chronic load, mapped to a 0-100 strain score.
# A normal day sits at the athlete's own average and scores 50.
STRAIN_ANCHORS = ((0.0, 0), (1.0, 50), (2.5, 90), (4.0, 100))

HIGH_STRAIN = 60
LOW_STRAIN = 25

# Hours before the next quality session, by how hard the session was.
RECOVERY_HOURS = {'easy': 0, 'moderate': 24, 'hard': 48, 'very_hard': 72}
MAX_RECOVERY_HOURS = 96

STRENGTH_TYPES = {'strength_training', 'indoor_cardio', 'fitness_equipment'}


def _activity_load(activity):
    raw = (activity or {}).get('raw') or {}
    return _number(raw.get('activityTrainingLoad')) or 0.0


def _activity_type(activity):
    raw = (activity or {}).get('raw') or {}
    return (activity or {}).get('type') or (raw.get('activityType') or {}).get('typeKey', '')


# Under this, a session has nothing to say: no load and barely any elapsed time.
# Garmin's watch produces stop_watch entries of a few seconds that would
# otherwise outrank a real run when picking the most recent session.
MIN_JUDGEABLE_SECONDS = 300


def is_judgeable(activity):
    """Whether a finished activity carries enough to be worth a verdict."""
    if _activity_load(activity) > 0:
        return True
    raw = (activity or {}).get('raw') or {}
    duration = _number((activity or {}).get('duration')) or _number(raw.get('duration')) or 0
    return duration >= MIN_JUDGEABLE_SECONDS


def daily_loads(activities, today=None, days=28):
    """Total training load per calendar day, newest day last."""
    today = today or date.today()
    start = today - timedelta(days=days - 1)
    totals = {}
    for activity in activities or []:
        day = _as_date(activity.get('date'))
        if not day or not (start <= day <= today):
            continue
        totals[day.isoformat()] = totals.get(day.isoformat(), 0.0) + _activity_load(activity)
    return totals


def reference_load(activities, today=None, days=28, chronic=None):
    """The athlete's own daily average load — Garmin's chronic value when we
    have it, otherwise derived from the last four weeks of activities."""
    given = _number(chronic)
    if given and given > 0:
        return given
    totals = daily_loads(activities, today=today, days=days)
    if not totals:
        return None
    return sum(totals.values()) / days


def strain_from_load(load, reference=None):
    """Map a day's load onto 0-100 through the anchor curve."""
    load = _number(load) or 0.0
    if load <= 0:
        return 0
    reference = _number(reference)
    if not reference or reference <= 0:
        reference = DEFAULT_REFERENCE_LOAD
    ratio = load / reference

    previous_ratio, previous_strain = STRAIN_ANCHORS[0]
    for anchor_ratio, anchor_strain in STRAIN_ANCHORS[1:]:
        if ratio <= anchor_ratio:
            span = anchor_ratio - previous_ratio
            share = (ratio - previous_ratio) / span if span else 0
            return round(previous_strain + share * (anchor_strain - previous_strain))
        previous_ratio, previous_strain = anchor_ratio, anchor_strain
    return 100


def strain_series(activities, today=None, days=14, reference=None):
    """Per-day strain for the trailing window, newest last."""
    today = today or date.today()
    if reference is None:
        reference = reference_load(activities, today=today)
    totals = daily_loads(activities, today=today, days=max(days, 28))
    series = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        load = totals.get(day.isoformat(), 0.0)
        series.append({
            't': day.isoformat(),
            'load': round(load, 1),
            'strain': strain_from_load(load, reference),
        })
    return series


def consecutive_high_days(series):
    """How many days in a row, counting back from the newest, sat above the
    high-strain line.  Two is a block; four without a break is a warning."""
    streak = 0
    for point in reversed(series or []):
        if point.get('strain', 0) >= HIGH_STRAIN:
            streak += 1
        else:
            break
    return streak


def strain_balance(strain, readiness):
    """How today's cost lines up with what the body signalled this morning."""
    strain = _number(strain) or 0
    readiness = _number(readiness)
    if readiness is None:
        return {'state': 'unknown', 'label': 'Saknar beredskapsdata',
                'detail': 'Utan CNS-beredskap går dagens belastning inte att väga mot återhämtningen.'}
    if strain >= HIGH_STRAIN and readiness < 45:
        return {'state': 'overreaching', 'label': 'Hård dag på tom tank',
                'detail': 'Du la en tung dos ovanpå låg beredskap. Det är så överbelastning byggs.'}
    if strain >= HIGH_STRAIN and readiness >= 60:
        return {'state': 'productive', 'label': 'Produktiv belastning',
                'detail': 'Hård dos på en kropp som var redo — det här är den träning som ger avkastning.'}
    if strain <= LOW_STRAIN and readiness >= 70:
        return {'state': 'undertrained', 'label': 'Utrymme kvar',
                'detail': 'Beredskapen var hög och dagen blev lätt. Du hade tålt mer.'}
    if strain <= LOW_STRAIN and readiness < 45:
        return {'state': 'recovering', 'label': 'Återhämtning pågår',
                'detail': 'Låg dos på låg beredskap — precis rätt prioritering idag.'}
    return {'state': 'balanced', 'label': 'I balans',
            'detail': 'Dagens belastning ligger i nivå med vad kroppen signalerade.'}


def strain_summary(activities, readiness=None, chronic=None, today=None, days=14):
    """Today's strain, the trailing block and one honest verdict."""
    today = today or date.today()
    reference = reference_load(activities, today=today, chronic=chronic)
    series = strain_series(activities, today=today, days=days, reference=reference)

    today_point = series[-1] if series else {'strain': 0, 'load': 0.0}
    recent = [point['strain'] for point in series[-7:]]
    week_avg = round(sum(recent) / len(recent)) if recent else 0
    streak = consecutive_high_days(series)
    balance = strain_balance(today_point['strain'], readiness)

    if streak >= 4:
        headline = 'Fjärde hårda dagen i rad'
        detail = ('Du har legat över den höga linjen fyra dygn i följd. '
                  'Lägg in en riktigt lätt dag innan nästa kvalitetspass.')
        tone = 'warn'
    elif balance['state'] == 'overreaching':
        headline = balance['label']
        detail = balance['detail']
        tone = 'warn'
    elif balance['state'] == 'undertrained':
        headline = balance['label']
        detail = balance['detail']
        tone = 'watch'
    elif balance['state'] == 'productive':
        headline = balance['label']
        detail = balance['detail']
        tone = 'good'
    else:
        headline = balance['label']
        detail = balance['detail']
        tone = 'neutral'

    return {
        'date': today.isoformat(),
        'strain': today_point['strain'],
        'load': today_point['load'],
        'weekAvgStrain': week_avg,
        'consecutiveHighDays': streak,
        'referenceLoad': round(reference, 1) if reference else None,
        'referenceSource': 'garmin' if _number(chronic) else ('history' if reference else 'default'),
        'lowConfidence': reference is None,
        'balance': balance,
        'headline': headline,
        'detail': detail,
        'tone': tone,
        'series': series,
    }


def session_intensity(load, reference=None):
    """Name the size of a single session against the athlete's own average."""
    strain = strain_from_load(load, reference)
    if strain >= 85:
        return 'very_hard'
    if strain >= 60:
        return 'hard'
    if strain >= 30:
        return 'moderate'
    return 'easy'


INTENSITY_LABELS = {
    'easy': 'Lätt pass', 'moderate': 'Måttligt pass',
    'hard': 'Hårt pass', 'very_hard': 'Mycket hårt pass',
}


def _recovery_actions(intensity, acwr, readiness, sleep_hours):
    """Concrete follow-ups, each tied to the reason it is being suggested."""
    actions = []
    if intensity in ('hard', 'very_hard'):
        actions.append({
            'title': 'Ät ordentligt inom två timmar',
            'why': 'Passet tömde glykogenet — påfyllningen avgör hur imorgon känns.',
        })
        actions.append({
            'title': 'Lätt eller vila imorgon',
            'why': f'{INTENSITY_LABELS[intensity]} behöver ett dygn innan nästa hårda dos.',
        })
    if _number(sleep_hours) is not None and _number(sleep_hours) < 7:
        actions.append({
            'title': 'Sikta på 8 timmar i natt',
            'why': f'Du sov {_number(sleep_hours):.1f} h senast — sömnen är just nu din trängsta faktor.',
        })
    if _number(acwr) is not None and _number(acwr) > 1.3:
        actions.append({
            'title': 'Håll nere volymen resten av veckan',
            'why': f'ACWR ligger på {_number(acwr):.2f}; över 1,3 stiger skaderisken.',
        })
    if _number(readiness) is not None and _number(readiness) < 45:
        actions.append({
            'title': 'Boka in en riktig vilodag',
            'why': 'CNS-beredskapen var låg redan innan passet.',
        })
    if not actions:
        actions.append({
            'title': 'Kör vidare enligt plan',
            'why': 'Passet kostade lagom mycket och inga varningssignaler sticker ut.',
        })
    return actions


def session_verdict(activity, reference=None, acwr=None, readiness=None,
                    sleep_hours=None, today=None):
    """What one finished session cost, and what it means for the next one."""
    today = today or date.today()
    load = _activity_load(activity)
    type_key = _activity_type(activity)
    intensity = session_intensity(load, reference)
    strain = strain_from_load(load, reference)

    hours = RECOVERY_HOURS[intensity]
    flags = []
    if _number(acwr) is not None and _number(acwr) > 1.3:
        hours += 24
        flags.append('high_acwr')
    if _number(readiness) is not None and _number(readiness) < 45:
        hours += 24
        flags.append('low_readiness')
    if _number(sleep_hours) is not None and _number(sleep_hours) < 6:
        hours += 24
        flags.append('sleep_deficit')
    if intensity in ('hard', 'very_hard'):
        flags.append('hard_session')
    hours = min(hours, MAX_RECOVERY_HOURS)

    session_day = _as_date(activity.get('date')) or today
    next_quality = session_day + timedelta(days=round(hours / 24))

    label = INTENSITY_LABELS[intensity]
    if load > 0:
        detail = f'Belastning {round(load)} — {label.lower()} för din nuvarande nivå.'
    else:
        detail = f'{label} utan registrerad belastning från Garmin.'
    if hours == 0:
        timing = 'Du kan lägga ett kvalitetspass redan imorgon.'
    else:
        timing = f'Nästa kvalitetspass tidigast {next_quality.isoformat()}.'

    return {
        'activityId': activity.get('id'),
        'name': activity.get('name'),
        'date': session_day.isoformat(),
        'type': type_key,
        'isRun': type_key in RUN_TYPES,
        'load': round(load, 1),
        'strain': strain,
        'intensity': intensity,
        'headline': label,
        'detail': detail,
        'timing': timing,
        'nextQualityDate': next_quality.isoformat(),
        'recoveryHours': hours,
        'recovery': _recovery_actions(intensity, acwr, readiness, sleep_hours),
        'flags': flags,
    }
