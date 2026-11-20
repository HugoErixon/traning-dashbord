"""VDOT — Jack Daniels' fitness number, and the training paces it gives.

VDOT is the VO2max a runner "behaves as if" they had, worked out from a race
time with the Daniels–Gilbert formulas. Its use here is the training paces:
every session kind gets its pace from one number, so easy, threshold and
interval targets move together as fitness changes instead of being guessed one
by one.

    vo2(v)  = -4.60 + 0.182258·v + 0.000104·v²      (v in m/min)
    %max(t) = 0.8 + 0.1894393·e^(-0.012778·t) + 0.2989558·e^(-0.1932605·t)   (t in min)
    VDOT    = vo2(distance / t) / %max(t)

Training paces are the speed at which vo2(v) equals a share of VDOT
(easy 62–74 %, threshold 88 %, interval 97.5 %), marathon pace is the
predicted marathon time, and repetition pace is 6 s per 400 m faster than
interval pace, as in Daniels' Running Formula. Checked against his tables:
VDOT 50 gives T 4:15/km and I 3:55/km, the same as the book.

No database or network access — callers pass in the numbers.
"""

import math

from session_analysis import format_pace

MIN_VDOT = 25.0
MAX_VDOT = 85.0

# Share of VDOT per zone (Daniels). Easy is a range; the others are one pace
# with a small band around it so a plan can say "3:55–4:00".
EASY_FRACTIONS = (0.62, 0.74)
THRESHOLD_FRACTION = 0.88
INTERVAL_FRACTION = 0.975
REPETITION_FASTER_SEC = 15  # 6 s per 400 m

MARATHON_KM = 42.195
HALF_KM = 21.0975

# Seconds per km either side of a zone's pace that still counts as on target.
ZONE_BANDS = {'T': (-4, 4), 'I': (-5, 5), 'R': (-5, 5), 'M': (-4, 4), 'HM': (-3, 5)}

# Session kinds from session_analysis.classify_session → Daniels zone.
KIND_TO_ZONE = {
    'interval': 'I',
    'threshold': 'T',
    'race': 'HM',
    'long': 'E',
    'easy': 'E',
    'run': 'E',
}

ZONE_LABELS = {
    'E': 'Lugnt / långpass',
    'M': 'Maratonfart',
    'T': 'Tröskel',
    'I': 'Intervall',
    'R': 'Repetition',
}


def _vo2(speed_m_min):
    return -4.60 + 0.182258 * speed_m_min + 0.000104 * speed_m_min ** 2


def _fraction(minutes):
    return (0.8 + 0.1894393 * math.exp(-0.012778 * minutes)
            + 0.2989558 * math.exp(-0.1932605 * minutes))


def _clean(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if MIN_VDOT <= value <= MAX_VDOT else None


def from_race(distance_km, seconds):
    """VDOT from a race (or a predicted race) over `distance_km` in `seconds`."""
    try:
        distance_km, seconds = float(distance_km), float(seconds)
    except (TypeError, ValueError):
        return None
    if distance_km < 1.5 or seconds <= 0:
        return None
    minutes = seconds / 60
    speed = distance_km * 1000 / minutes
    value = _vo2(speed) / _fraction(minutes)
    return round(value, 1) if _clean(value) else None


def _speed_at(vo2):
    # vo2(v) = vo2 solved for v (positive root).
    a, b, c = 0.000104, 0.182258, -4.60 - vo2
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)


def _pace_at_fraction(vdot, fraction):
    return 60000 / _speed_at(vdot * fraction)


def race_time(vdot, distance_km):
    """Predicted race time in seconds for a VDOT (inverse of from_race)."""
    vdot = _clean(vdot)
    if vdot is None:
        return None
    meters = distance_km * 1000
    low, high = 60.0, 600 * 60.0  # one minute to ten hours
    for _ in range(80):
        mid = (low + high) / 2
        minutes = mid / 60
        if _vo2(meters / minutes) / _fraction(minutes) > vdot:
            low = mid  # still faster than this VDOT allows → slow down
        else:
            high = mid
    return round((low + high) / 2)


def from_threshold_pace(lt_pace_sec):
    """VDOT whose threshold pace is `lt_pace_sec` per km (Garmin's measured value)."""
    try:
        pace = float(lt_pace_sec)
    except (TypeError, ValueError):
        return None
    if not 150 <= pace <= 600:
        return None
    value = _vo2(60000 / pace) / THRESHOLD_FRACTION
    return round(value, 1) if _clean(value) else None


def _range_text(low, high):
    return f"{format_pace(low).replace('/km', '')}–{format_pace(high)}"


def _band(pace, zone):
    low, high = ZONE_BANDS[zone]
    return {'lowSec': round(pace + low), 'highSec': round(pace + high)}


def paces(vdot):
    """Training paces in seconds per km for every Daniels zone."""
    vdot = _clean(vdot)
    if vdot is None:
        return None
    easy_fast = _pace_at_fraction(vdot, EASY_FRACTIONS[1])
    easy_slow = _pace_at_fraction(vdot, EASY_FRACTIONS[0])
    threshold = _pace_at_fraction(vdot, THRESHOLD_FRACTION)
    interval = _pace_at_fraction(vdot, INTERVAL_FRACTION)
    repetition = interval - REPETITION_FASTER_SEC
    marathon = race_time(vdot, MARATHON_KM) / MARATHON_KM
    half = race_time(vdot, HALF_KM) / HALF_KM
    zones = {
        'E': {'paceSec': round((easy_fast + easy_slow) / 2),
              'lowSec': round(easy_fast), 'highSec': round(easy_slow)},
        'M': {'paceSec': round(marathon), **_band(marathon, 'M')},
        'T': {'paceSec': round(threshold), **_band(threshold, 'T')},
        'I': {'paceSec': round(interval), **_band(interval, 'I')},
        'R': {'paceSec': round(repetition), **_band(repetition, 'R')},
        'HM': {'paceSec': round(half), **_band(half, 'HM')},
    }
    for key, zone in zones.items():
        zone['key'] = key
        zone['label'] = ZONE_LABELS.get(key, 'Halvmaratonfart')
        zone['pace'] = format_pace(zone['paceSec'])
        zone['text'] = _range_text(zone['lowSec'], zone['highSec'])
    # Per 400 m is how repetition and interval reps are usually run on a track.
    zones['I']['per400Sec'] = round(zones['I']['paceSec'] * 0.4)
    zones['R']['per400Sec'] = round(zones['R']['paceSec'] * 0.4)
    return zones


def band_for_kind(kind, vdot):
    """The pace band for a session kind, in the shape pace_progression uses."""
    zones = paces(vdot)
    zone = KIND_TO_ZONE.get(kind)
    if not zones or not zone:
        return None
    z = zones[zone]
    return {'kind': kind, 'zone': zone, 'paceSec': z['paceSec'], 'lowSec': z['lowSec'],
            'highSec': z['highSec'], 'text': z['text']}


def current(prediction=None, races=None, lt_pace_sec=None, manual=None, today=None,
            race_max_age_days=60):
    """Choose today's VDOT and say where it came from.

    Order: the athlete's own number, then Trainyze's race predictor (its half
    marathon is what the model reads off at threshold heart rate, so that
    distance is used), then a recent race, then Garmin's threshold pace. A race
    is only the main source when the predictor has nothing — a single hot,
    hilly race day must not set every training pace — but it is always listed
    for comparison."""
    candidates = []
    manual = _clean(manual)
    if manual:
        candidates.append({'source': 'manual', 'vdot': round(manual, 1),
                           'label': 'Ditt eget värde'})

    if isinstance(prediction, dict) and prediction.get('available'):
        half = next((p for p in prediction.get('predictions') or [] if p.get('key') == 'half'), None)
        value = from_race(HALF_KM, half['seconds']) if half else None
        if value:
            candidates.append({'source': 'predictor', 'vdot': value,
                               'label': f"Trainyzes prognos (halvmaraton {half['time']})"})

    best_race = None
    for race in races or []:
        day = race.get('date')
        if today and day and (today - day).days > race_max_age_days:
            continue
        seconds = race.get('gapSeconds') or race.get('seconds')
        value = from_race(race.get('km'), seconds)
        if value and (best_race is None or value > best_race['vdot']):
            best_race = {'source': 'race', 'vdot': value,
                         'label': f"{race.get('name') or 'Lopp'} {day.isoformat() if day else ''}".strip(),
                         'date': day.isoformat() if day else None}
    if best_race:
        candidates.append(best_race)

    value = from_threshold_pace(lt_pace_sec)
    if value:
        candidates.append({'source': 'threshold', 'vdot': value,
                           'label': f'Garmins tröskelfart {format_pace(lt_pace_sec)}'})

    if not candidates:
        return {'available': False,
                'reason': 'Det finns ingen prognos, inget lopp och ingen tröskelfart att räkna VDOT från ännu.'}

    chosen = candidates[0]
    return {
        'available': True,
        'vdot': chosen['vdot'],
        'source': chosen['source'],
        'sourceLabel': chosen['label'],
        'paces': paces(chosen['vdot']),
        'alternatives': [c for c in candidates[1:]],
        'equivalents': {key: race_time(chosen['vdot'], km)
                        for key, km in (('5k', 5.0), ('10k', 10.0), ('half', HALF_KM),
                                        ('marathon', MARATHON_KM))},
    }


def goal_vdot(distance_km, seconds):
    """The VDOT a race goal demands."""
    return from_race(distance_km, seconds)


def describe(info, goal=None):
    """Compact English lines for the LLM prompts."""
    if not info or not info.get('available'):
        return ''
    z = info['paces']
    lines = [
        f"VDOT {info['vdot']} (Daniels; source: {info['sourceLabel']}). "
        'Every running pace in the plan comes from these zones:',
        f"  E (easy, long runs, recovery): {z['E']['text']}",
        f"  M (marathon pace): {z['M']['pace']}",
        f"  HM (half-marathon race pace): {z['HM']['pace']}",
        f"  T (threshold / tempo / cruise intervals, max ~20 min continuous or 3–5 × 6–10 min): {z['T']['text']}",
        f"  I (VO2max intervals, 3–5 min reps, jog recovery about as long as the rep): {z['I']['text']}",
        f"  R (repetitions 200–400 m, full recovery): {z['R']['text']} ({z['R']['per400Sec']} s per 400 m)",
    ]
    if goal and goal.get('vdot'):
        gap = round(goal['vdot'] - info['vdot'], 1)
        lines.append(
            f"Goal {goal['label']} demands VDOT {goal['vdot']}"
            + (f" — {gap} above today." if gap > 0 else ' — already at or below today.'))
    lines.append(
        "Write the zone letter and pace in every running session's detail, e.g. "
        f"\"2 km uppvärmning E · 4×8 min T {z['T']['pace']}, 1 min vila · 2 km nedjogg E\".")
    return '\n'.join(lines)
