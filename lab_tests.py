"""Lab test results (lactate threshold / VO2max test) and what follows from them.

A lab test measures what the watch only estimates, so its threshold heart rate
replaces Garmin's in the race predictor and its threshold pace becomes a VDOT
source ahead of Garmin's threshold pace. Heart-rate zones are written from the
two thresholds the test found: LT1 (aerobic, roughly 2 mmol) and LT2 (anaerobic,
roughly 4 mmol / the turn point).
"""

import re
from datetime import date

MAX_TESTS = 10


def parse_pace(value):
    """'4:05' or '4.05' (min/km) or a number of seconds → seconds per km."""
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        sec = float(value)
    else:
        text = str(value).strip().replace(',', '.')
        m = re.fullmatch(r'(\d{1,2})[:.](\d{2})(?:\s*/?\s*km)?', text)
        if not m:
            raise ValueError('Fart skrivs som min:sek per km, t.ex. 4:05.')
        sec = int(m.group(1)) * 60 + int(m.group(2))
    if not 150 <= sec <= 600:
        raise ValueError('Farten måste ligga mellan 2:30 och 10:00 per km.')
    return int(round(sec))


def speed_to_pace(kmh):
    """Löpbandstester anger ofta km/h; 15 km/h = 4:00/km."""
    kmh = float(str(kmh).replace(',', '.'))
    if not 6 <= kmh <= 25:
        raise ValueError('Hastigheten måste vara 6–25 km/h.')
    return int(round(3600 / kmh))


def _bounded_int(data, key, low, high, label):
    value = data.get(key)
    if value in (None, ''):
        return None
    try:
        value = int(round(float(str(value).replace(',', '.'))))
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} måste vara ett tal.') from exc
    if not low <= value <= high:
        raise ValueError(f'{label} måste vara {low}–{high}.')
    return value


def normalize(data, today=None):
    """Validate one test from the form. Raises ValueError with a Swedish message."""
    today = today or date.today()
    try:
        day = date.fromisoformat(str(data.get('date') or '')[:10])
    except ValueError as exc:
        raise ValueError('Ange testets datum.') from exc
    if day > today:
        raise ValueError('Testet kan inte ligga i framtiden.')

    def pace(key, speed_key):
        if data.get(speed_key) not in (None, ''):
            return speed_to_pace(data[speed_key])
        return parse_pace(data.get(key))

    test = {
        'date': day.isoformat(),
        'lt1Hr': _bounded_int(data, 'lt1Hr', 100, 200, 'Pulsen vid LT1'),
        'lt1PaceSec': pace('lt1Pace', 'lt1Speed'),
        'lt2Hr': _bounded_int(data, 'lt2Hr', 130, 215, 'Pulsen vid LT2'),
        'lt2PaceSec': pace('lt2Pace', 'lt2Speed'),
        'vo2max': _bounded_int(data, 'vo2max', 30, 95, 'VO2max'),
        'maxHr': _bounded_int(data, 'maxHr', 150, 230, 'Maxpulsen'),
        'note': str(data.get('note') or '').strip()[:300],
    }
    if not any(test[k] for k in ('lt1Hr', 'lt2Hr', 'lt1PaceSec', 'lt2PaceSec', 'vo2max', 'maxHr')):
        raise ValueError('Fyll i minst ett värde från testet.')
    if test['lt1Hr'] and test['lt2Hr'] and test['lt1Hr'] >= test['lt2Hr']:
        raise ValueError('Pulsen vid LT1 ska vara lägre än vid LT2.')
    if test['lt2Hr'] and test['maxHr'] and test['lt2Hr'] >= test['maxHr']:
        raise ValueError('Tröskelpulsen ska vara lägre än maxpulsen.')
    if test['lt1PaceSec'] and test['lt2PaceSec'] and test['lt1PaceSec'] <= test['lt2PaceSec']:
        raise ValueError('Farten vid LT1 ska vara långsammare än vid LT2.')
    return test


def add(tests, test):
    """Insert or replace the test on that date; newest first."""
    kept = [t for t in tests or [] if t.get('date') != test['date']]
    return sorted(kept + [test], key=lambda t: t['date'], reverse=True)[:MAX_TESTS]


def fmt_pace(sec):
    if not sec:
        return None
    sec = int(round(sec))
    return f'{sec // 60}:{sec % 60:02d}/km'


def hr_zones(test):
    """Five heart-rate zones from the two thresholds (or None)."""
    lt1, lt2, max_hr = test.get('lt1Hr'), test.get('lt2Hr'), test.get('maxHr')
    if not (lt1 and lt2):
        return None
    top = max_hr or lt2 + 12
    return [
        {'key': 'Z1', 'label': 'Återhämtning', 'low': None, 'high': lt1 - 11},
        {'key': 'Z2', 'label': 'Lugnt / långpass', 'low': lt1 - 10, 'high': lt1},
        {'key': 'Z3', 'label': 'Mellan trösklarna (maraton–HM)', 'low': lt1 + 1, 'high': lt2 - 6},
        {'key': 'Z4', 'label': 'Tröskel', 'low': lt2 - 5, 'high': lt2 + 2},
        {'key': 'Z5', 'label': 'Över tröskeln (intervaller)', 'low': lt2 + 3, 'high': top},
    ]


def zone_range(zone):
    if zone['low'] is None:
        return f"<{zone['high'] + 1}"
    return f"{zone['low']}–{zone['high']}"


def describe(test):
    """English lines for the planning prompts."""
    if not test:
        return ''
    bits = []
    if test.get('lt1Hr') or test.get('lt1PaceSec'):
        bits.append('LT1 (aerobic threshold) '
                    + ' at '.join(x for x in (f"{test['lt1Hr']} bpm" if test.get('lt1Hr') else None,
                                               fmt_pace(test.get('lt1PaceSec'))) if x))
    if test.get('lt2Hr') or test.get('lt2PaceSec'):
        bits.append('LT2 (anaerobic threshold) '
                    + ' at '.join(x for x in (f"{test['lt2Hr']} bpm" if test.get('lt2Hr') else None,
                                               fmt_pace(test.get('lt2PaceSec'))) if x))
    if test.get('vo2max'):
        bits.append(f"VO2max {test['vo2max']} ml/kg/min")
    if test.get('maxHr'):
        bits.append(f"max HR {test['maxHr']}")
    lines = [f"LAB TEST {test['date']} (measured — overrides the watch's estimates): " + ' · '.join(bits) + '.']
    zones = hr_zones(test)
    if zones:
        lines.append('Heart-rate zones from the test: ' + ', '.join(
            f"{z['key']} {z['label']} {zone_range(z)}" for z in zones)
            + '. Easy runs stay at or below the LT1 heart rate; threshold work sits in Z4.')
    if test.get('note'):
        lines.append(f"Test note: {test['note']}")
    return '\n'.join(lines)
