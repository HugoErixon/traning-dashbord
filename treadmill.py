"""Belt speeds for treadmill runs.

On a treadmill the watch guesses pace from the wrist (or a foot pod), so a
4 × 2 km at exactly 15 km/h comes back as 3:46–3:50/km with a "+0.9 % fade"
that never happened. The belt speed is the truth, and only the athlete knows
it, so they enter it per lap and it replaces the watch's speed and distance
for that lap. Until they have, a treadmill run's pace is not judged at all.

Speeds are stored per activity in cache `treadmill-speeds:v1:<activityId>`
as {"laps": {"<garmin lap position>": km/h}}. Nothing here touches the
database or the network.
"""

import re

CACHE_KEY = 'treadmill-speeds:v1:{}'

MIN_KMH = 3.0
MAX_KMH = 30.0
# Same cut-off as session_analysis.normalize_laps: shorter laps are standing rests.
STANDING_REST_M = 50

_PACE_RE = re.compile(r'^(\d{1,2}):(\d{2})\s*(?:/\s*km)?$')
_SPEED_RE = re.compile(r'^(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:km/?h|kmh|km/t)?$', re.I)


def is_treadmill(activity):
    """Garmin's typeKey (raw activity) or the stored `type` column."""
    activity = activity or {}
    type_key = ((activity.get('activityType') or {}).get('typeKey')
                or activity.get('type') or '')
    return 'treadmill' in str(type_key).lower()


def parse_speed(value):
    """'15', '15,1', '15 km/h' -> km/h; '4:00' (pace) -> 15.0. Empty -> None.

    Raises ValueError for anything else, so a typo is reported instead of
    silently becoming a wrong pace.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError('Ogiltig hastighet.')
    if isinstance(value, (int, float)):
        kmh = float(value)
    else:
        text = str(value).strip()
        pace = _PACE_RE.match(text)
        if pace:
            seconds = int(pace.group(1)) * 60 + int(pace.group(2))
            if seconds <= 0:
                raise ValueError('Ogiltigt tempo.')
            kmh = 3600 / seconds
        else:
            speed = _SPEED_RE.match(text)
            if not speed:
                raise ValueError(f'Förstår inte "{text}". Skriv km/h (15,1) eller tempo (4:00).')
            kmh = float(speed.group(1).replace(',', '.'))
    if not MIN_KMH <= kmh <= MAX_KMH:
        raise ValueError(f'Hastigheten måste ligga mellan {MIN_KMH:g} och {MAX_KMH:g} km/h.')
    return round(kmh, 2)


def clean_speeds(payload):
    """Validate a client payload {'laps': {position: speed}} -> {str(pos): kmh}."""
    laps = (payload or {}).get('laps')
    if not isinstance(laps, dict):
        raise ValueError('laps saknas.')
    out = {}
    for key, value in laps.items():
        try:
            position = int(key)
        except (TypeError, ValueError):
            raise ValueError('Ogiltigt varvnummer.') from None
        if position < 0 or position > 500:
            raise ValueError('Ogiltigt varvnummer.')
        kmh = parse_speed(value)
        if kmh is not None:
            out[str(position)] = kmh
    return out


def lap_speeds(stored):
    """Stored cache value -> {int position: km/h}."""
    laps = (stored or {}).get('laps') or {}
    out = {}
    for key, value in laps.items():
        try:
            out[int(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def apply_to_laps(laps, speeds):
    """Replace the watch's speed and distance with the belt's, lap by lap.

    `laps` is session_analysis.normalize_laps output, whose `idx` is Garmin's
    lap position. A lap without an entered speed keeps the watch's numbers.
    """
    out = []
    for lap in laps or []:
        kmh = (speeds or {}).get(lap.get('idx'))
        if kmh:
            speed = kmh / 3.6
            lap = dict(lap, speed=speed, distanceM=speed * (lap.get('durationSec') or 0),
                       beltKmh=kmh)
        out.append(lap)
    return out


def corrected_distance_m(laps):
    """Total distance from belt-corrected laps, or None when no lap was corrected."""
    if not any(lap.get('beltKmh') for lap in laps or []):
        return None
    return sum(lap.get('distanceM') or 0 for lap in laps)


def apply_to_detail(detail, speeds):
    """Belt speeds into activity_detail's laps and overview (a copy; the cache keeps Garmin's).

    Lap positions match: activity_detail._laps keeps Garmin's lap order.
    """
    detail = dict(detail or {})
    if not speeds:
        return detail
    laps = []
    for position, lap in enumerate(detail.get('laps') or []):
        kmh = speeds.get(position)
        duration = (lap or {}).get('duration')
        # Ståvila: klockan registrerar nästan ingen sträcka, och då ska ingen fart läggas på.
        if kmh and duration and ((lap or {}).get('distance') or 0) >= STANDING_REST_M:
            lap = dict(lap, distance=round(kmh / 3.6 * duration, 1),
                       pace=round(3600 / kmh, 1), beltKmh=kmh)
        laps.append(lap)
    detail['laps'] = laps
    if any(lap.get('beltKmh') for lap in laps):
        overview = dict(detail.get('overview') or {})
        distance = sum(lap.get('distance') or 0 for lap in laps)
        moving = overview.get('movingDuration') or overview.get('duration')
        if distance > 0:
            overview['distance'] = round(distance, 1)
            if moving:
                overview['pace'] = round(moving / (distance / 1000), 1)
        detail['overview'] = overview
        detail['beltSpeeds'] = True
    return detail
