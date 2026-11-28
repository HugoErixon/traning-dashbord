"""User-entered running preferences and zones. No AI or external services."""
import math
import re

CACHE_KEY = 'runner-profile:v1'
PACE_KEYS = ('easy', 'steady', 'marathon', 'threshold', 'interval')


def _text(data, key, limit):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise ValueError(f'{key}: ange text på högst {limit} tecken.')
    return value.strip()


def _number(value, label, low, high, integer=False):
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError(f'{label}: ange ett tal.')
    try:
        result = float(str(value).replace(',', '.'))
    except (ValueError, TypeError):
        raise ValueError(f'{label}: ange ett tal.') from None
    if not math.isfinite(result) or not low <= result <= high or (integer and result != int(result)):
        raise ValueError(f'{label}: ange ett {"heltal " if integer else "tal "}mellan {low} och {high}.')
    return int(result) if integer else result


def pace_seconds(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,2}:[0-5]\d', value.strip()):
        raise ValueError('Skriv tempo som min:sek per km, till exempel 5:30.')
    minutes, seconds = map(int, value.strip().split(':'))
    return _number(minutes * 60 + seconds, 'Tempo (sekunder/km)', 120, 1800, True)


def normalize(data):
    if not isinstance(data, dict):
        raise ValueError('Profilen måste vara ett objekt.')
    profile = {key: _text(data, key, limit) for key, limit in
               [('displayName', 80), ('about', 1000), ('trainingNotes', 1000)]}
    experience = data.get('experience', '')
    if experience not in ('', 'new', 'recreational', 'experienced', 'competitive'):
        raise ValueError('Välj en löparbakgrund i listan.')
    profile['experience'] = experience
    profile['weeklyKm'] = _number(data.get('weeklyKm'), 'Nuvarande km/vecka', 0, 350)
    profile['sessionsPerWeek'] = _number(data.get('sessionsPerWeek'), 'Pass/vecka', 1, 14, True)
    days = data.get('preferredDays', [])
    if not isinstance(days, list) or any(type(d) is not int or d not in range(7) for d in days):
        raise ValueError('Träningsdagar måste vara veckodagar mellan 0 och 6.')
    profile['preferredDays'] = sorted(set(days))
    long_day = data.get('longRunDay')
    if long_day is not None and (type(long_day) is not int or long_day not in days):
        raise ValueError('Långpassdagen behöver vara en av dina valda träningsdagar.')
    profile['longRunDay'] = long_day
    profile['maxHr'] = _number(data.get('maxHr'), 'Maxpuls', 80, 250, True)
    profile['restingHr'] = _number(data.get('restingHr'), 'Vilopuls', 25, 150, True)
    if profile['maxHr'] and profile['restingHr'] and profile['restingHr'] >= profile['maxHr']:
        raise ValueError('Vilopulsen behöver vara lägre än maxpulsen.')
    hr = data.get('hrZones', [])
    if not isinstance(hr, list) or len(hr) not in (0, 5):
        raise ValueError('Ange fem pulszoner, eller lämna alla tomma.')
    profile['hrZones'] = []
    previous = None
    for i, zone in enumerate(hr):
        if not isinstance(zone, dict):
            raise ValueError('Ogiltig pulszon.')
        low = _number(zone.get('min'), f'Z{i + 1} från', 25, 250, True)
        high = _number(zone.get('max'), f'Z{i + 1} till', 25, 250, True)
        if low is None or high is None or low >= high:
            raise ValueError(f'Z{i + 1}: ange både nedre och övre puls, med lägre puls först.')
        if previous is not None and low != previous + 1:
            raise ValueError(f'Z{i + 1} ska börja på {previous + 1} bpm, direkt efter föregående zon.')
        if profile['maxHr'] and high > profile['maxHr']:
            raise ValueError('En pulszon kan inte sluta över din angivna maxpuls.')
        profile['hrZones'].append({'min': low, 'max': high})
        previous = high
    paces = data.get('paceZones', {})
    if not isinstance(paces, dict) or set(paces) - set(PACE_KEYS):
        raise ValueError('Ogiltiga tempozoner.')
    profile['paceZones'] = {}
    for key, zone in paces.items():
        if not isinstance(zone, dict):
            raise ValueError('Ogiltig tempozon.')
        fast, slow = pace_seconds(zone.get('fast')), pace_seconds(zone.get('slow'))
        if fast is None and slow is None:
            continue
        if fast is None or slow is None or fast > slow:
            raise ValueError('Ange både snabbaste och långsammaste tempo, med snabbaste först.')
        profile['paceZones'][key] = {'fast': f'{fast // 60}:{fast % 60:02}',
                                     'slow': f'{slow // 60}:{slow % 60:02}'}
    return profile


def payload(profile):
    """Explicit units and provenance for future planning consumers."""
    return {'profile': profile, 'summary': {
        'preferredDayCount': len(profile['preferredDays']),
        'hrSource': 'manual' if profile['hrZones'] else 'unset',
        'paceSource': 'manual' if profile['paceZones'] else 'unset',
        'paceRanges': {key: {'fastSecPerKm': pace_seconds(zone['fast']),
                             'slowSecPerKm': pace_seconds(zone['slow'])}
                       for key, zone in profile['paceZones'].items()},
    }}
