"""Trainyzes planmotor: varje dag i en vecka, byggd på regler och inte på AI.

Säsongsplanen (season_plan) bestämmer ramen för veckan: fas, km och långpass.
Den här modulen fyller ramen med konkreta pass, dag för dag, och visar vilka
regler planen följer. Allt är rena funktioner utan databas eller nätverk.

Forskningen bakom reglerna:

* Daniels' Running Formula (VDOT). Alla tempon kommer från ett enda tal. Per
  pass gäller: T ≤ 10 % av veckans km, I ≤ min(8 %, 10 km), R ≤ min(5 %, 8 km).
  Långpasset ≤ 30 % av veckans km och ≤ 150 min.
* Seiler: polariserad eller pyramidal fördelning. Omkring 80 % av tiden går
  lugnt, högst tre hårda dagar per vecka, med minst 48 h mellan dem.
* Den norska tröskelmodellen (Bakken, Ingebrigtsen, "Norwegian singles").
  Kvaliteten är främst tröskel något under T-fart (sub-tröskel, ungefär
  2–3 mmol laktat). Den delas upp i drag med kort vila, så att mycket tid
  hamnar strax under tröskeln utan att man blir sliten. Elitnivå kan köra
  dubbeltröskel: två sub-tröskelpass samma dag.
* Bosquet m.fl. 2007 (metaanalys om nedtrappning). Volymen sänks 40–60 %
  och intensiteten behålls. Det sköter säsongsramen; den här modulen
  behåller fartinslagen i nedtrappningen.
* Rønnestad och Mujika 2014 (styrka för uthållighetsidrottare). Tung styrka
  läggs på hårda dagar, så att de lätta dagarna förblir lätta.
"""

from itertools import combinations
from math import floor

DAY_NAMES = ['Måndag', 'Tisdag', 'Onsdag', 'Torsdag', 'Fredag', 'Lördag', 'Söndag']

# Antal kvalitetspass utöver långpasset, per nivå (runner_profile.experience).
QUALITY_BY_LEVEL = {'new': 1, 'recreational': 1, 'experienced': 2, 'competitive': 3, '': 2}
DEFAULT_DAYS = {
    3: [1, 3, 6], 4: [1, 3, 5, 6], 5: [0, 1, 3, 5, 6], 6: [0, 1, 2, 3, 5, 6],
    7: [0, 1, 2, 3, 4, 5, 6],
}

# Sub-tröskel ligger lite långsammare än Daniels T: sekunder per km.
SUB_T_SLOWER = (4, 10)
WARMUP_KM = 2.5
COOLDOWN_KM = 2.0
MIN_EASY_KM = 4.0
MIN_EASY_KM_SHORT = 3.0
STRIDES = '6 × 20 s stegringar, gå tillbaka'

# Puls som andel av tröskelpulsen (Friels löpzoner).
HR_SHARE = {'E': (0.75, 0.88), 'M': (0.89, 0.93), 'SUBT': (0.92, 0.97), 'T': (0.95, 1.0),
            'I': (1.0, 1.04), 'R': (None, None)}

RESEARCH = [
    {'key': 'daniels', 'title': 'Daniels: VDOT och tempozoner',
     'text': 'Alla tempon räknas fram ur ditt VDOT. Varje zon har sitt syfte: E bygger bas, '
             'T höjer tröskeln, I höjer VO2max, R förbättrar löpekonomi och snabbhet.'},
    {'key': 'seiler', 'title': 'Seiler: 80 % lugnt',
     'text': 'Elitlöpare tränar omkring 80 % av tiden lugnt och högst tre hårda pass per vecka. '
             'De lätta dagarna gör det möjligt att köra de hårda med kvalitet.'},
    {'key': 'norwegian', 'title': 'Norska modellen: mycket tröskel, kontrollerat',
     'text': 'Mycket tid strax under tröskeln, uppdelat i drag med kort vila, så att laktatet '
             'stannar runt 2–3 mmol. Du får stor träningseffekt utan att bli sliten till nästa pass.'},
    {'key': 'overload', 'title': 'Gradvis ökning och avlastning',
     'text': 'Volymen ökar stegvis mot toppen, och var fjärde vecka avlastas med omkring 20 % mindre volym.'},
    {'key': 'taper', 'title': 'Nedtrappning (Bosquet 2007)',
     'text': 'Under de sista 1–2 veckorna sänks volymen med 40–60 %, men fartinslagen behålls. '
             'Det ger störst formtopp.'},
    {'key': 'strength', 'title': 'Styrka på hårda dagar',
     'text': 'Tung styrka förbättrar löpekonomin. Den läggs samma dag som ett kvalitetspass, '
             'så att återhämtningsdagarna förblir lätta.'},
]


def fmt_pace(sec):
    sec = int(round(sec))
    return f'{sec // 60}:{sec % 60:02d}'


def _zone(zones, key):
    """(snabb, långsam, mitt) i s/km för en zon, även sub-tröskel."""
    if key == 'SUBT':
        t = zones['T']['paceSec']
        return t + SUB_T_SLOWER[0], t + SUB_T_SLOWER[1], t + sum(SUB_T_SLOWER) / 2
    z = zones[key]
    return z['lowSec'], z['highSec'], z['paceSec']


def pace_text(zones, key):
    fast, slow, _ = _zone(zones, key)
    return f'{fmt_pace(fast)}–{fmt_pace(slow)}/km'


def hr_text(key, lthr=None):
    low, high = HR_SHARE.get(key, (None, None))
    if not lthr or low is None:
        return ''
    if key == 'E':
        return f'puls under {round(lthr * high)}'
    return f'puls {round(lthr * low)}–{round(lthr * high)}'


ZONE_NAMES = {'E': 'lugnt', 'M': 'maratonfart', 'HM': 'halvmaratonfart', 'SUBT': 'sub-tröskel',
              'T': 'tröskel', 'I': 'intervall', 'R': 'repetition'}

# Kvalitetspassen. reps × (min eller m) i en zon med vila. Listorna är ordnade
# som en progression: index i fasen väljer pass, så veckorna blir längre och svårare.
LIBRARY = {
    'subt_short': [
        {'reps': 10, 'meters': 1000, 'zone': 'SUBT', 'rest': '60 s jogg'},
        {'reps': 6, 'min': 5, 'zone': 'SUBT', 'rest': '60 s jogg'},
        {'reps': 5, 'min': 6, 'zone': 'SUBT', 'rest': '60 s jogg'},
        {'reps': 8, 'meters': 1000, 'zone': 'SUBT', 'rest': '60 s jogg'},
        {'reps': 25, 'meters': 400, 'zone': 'SUBT', 'rest': '30 s stå'},
        {'reps': 6, 'min': 6, 'zone': 'SUBT', 'rest': '60 s jogg'},
    ],
    'subt_long': [
        {'reps': 4, 'min': 8, 'zone': 'SUBT', 'rest': '90 s jogg'},
        {'reps': 3, 'min': 10, 'zone': 'SUBT', 'rest': '2 min jogg'},
        {'reps': 5, 'meters': 2000, 'zone': 'SUBT', 'rest': '90 s jogg'},
        {'reps': 4, 'min': 10, 'zone': 'SUBT', 'rest': '2 min jogg'},
        {'reps': 3, 'meters': 3000, 'zone': 'SUBT', 'rest': '2 min jogg'},
    ],
    'threshold': [
        {'reps': 4, 'min': 6, 'zone': 'T', 'rest': '60 s jogg'},
        {'reps': 3, 'min': 10, 'zone': 'T', 'rest': '2 min jogg'},
        {'reps': 2, 'min': 15, 'zone': 'T', 'rest': '3 min jogg'},
        {'reps': 1, 'min': 20, 'zone': 'T', 'rest': None},
        {'reps': 2, 'min': 20, 'zone': 'T', 'rest': '3 min jogg'},
    ],
    'vo2': [
        {'reps': 5, 'meters': 1000, 'zone': 'I', 'rest': '2:30 jogg'},
        {'reps': 6, 'meters': 800, 'zone': 'I', 'rest': '2 min jogg'},
        {'reps': 5, 'min': 3, 'zone': 'I', 'rest': '2 min jogg'},
        {'reps': 4, 'meters': 1200, 'zone': 'I', 'rest': '3 min jogg'},
        {'reps': 6, 'meters': 1000, 'zone': 'I', 'rest': '2:30 jogg'},
    ],
    'speed': [
        {'reps': 8, 'meters': 200, 'zone': 'R', 'rest': '200 m jogg'},
        {'reps': 10, 'meters': 300, 'zone': 'R', 'rest': '300 m jogg'},
        {'reps': 6, 'meters': 400, 'zone': 'R', 'rest': '400 m jogg'},
        {'reps': 8, 'meters': 400, 'zone': 'R', 'rest': '400 m jogg'},
    ],
    'hills': [
        {'reps': 8, 'sec': 60, 'zone': 'I', 'rest': 'jogg ner', 'hill': True},
        {'reps': 10, 'sec': 75, 'zone': 'I', 'rest': 'jogg ner', 'hill': True},
        {'reps': 6, 'sec': 120, 'zone': 'I', 'rest': 'jogg ner', 'hill': True},
    ],
    'race_pace': [
        {'reps': 3, 'meters': 3000, 'zone': 'RACE', 'rest': '2 min jogg'},
        {'reps': 4, 'meters': 3000, 'zone': 'RACE', 'rest': '2 min jogg'},
        {'reps': 2, 'meters': 5000, 'zone': 'RACE', 'rest': '3 min jogg'},
        {'reps': 3, 'meters': 4000, 'zone': 'RACE', 'rest': '2 min jogg'},
        {'reps': 2, 'meters': 6000, 'zone': 'RACE', 'rest': '3 min jogg'},
    ],
}

PURPOSE = {
    'subt_short': 'Höjer laktattröskeln med mycket tid strax under tröskeln. Korta drag och kort '
                  'vila håller laktatet lågt (norska modellen). Ska kännas kontrollerat, aldrig maximalt.',
    'subt_long': 'Längre sub-tröskeldrag för uthållighet vid hög fart. Du bygger förmågan att hålla '
                 'farten utan att laktatet drar iväg.',
    'threshold': 'Klassisk Daniels-tröskel: "bekvämt jobbigt", ungefär den fart du klarar i en '
                 'timme. Höjer tröskeln och lär kroppen att ta hand om laktat.',
    'vo2': 'VO2max-intervaller på 3–5 min. Höjer taket för syreupptaget och gör tröskelfarten '
           'relativt lättare.',
    'speed': 'Korta snabba drag med full vila. Förbättrar löpekonomi och nervsystemets snabbhet '
             'utan att ge laktattrötthet.',
    'hills': 'Backintervaller: styrka och VO2max med mindre stöt mot benen än på plan mark.',
    'race_pace': 'Loppspecifikt: lär kroppen och huvudet exakt den fart du ska springa loppet i.',
    'long': 'Långpasset bygger aerob kapacitet, kapillärer, fettförbränning och mental uthållighet.',
    'long_mp': 'Långpass med loppfart på slutet: specifik uthållighet, att hålla farten trött.',
    'easy': 'Lugn distans bygger aerob bas och ger återhämtning. Håll det verkligen lugnt, så '
            'att de hårda passen blir bra.',
    'recovery': 'Återhämtningsjogg: blodcirkulation utan träningsstress.',
    'strength': 'Tung styrka förbättrar löpekonomi och skadetålighet. Den ligger på en hård dag '
                'så att de lätta dagarna förblir lätta.',
    'race': 'Tävling: dagens viktigaste pass.',
}

# Vilka kvalitetspass en fas använder, i prioritetsordning.
# Distansens loppfart och huvudpass per fas.
PHASE_QUALITY = {
    '5k': {'base': ['subt_short', 'hills', 'subt_long'], 'specific': ['vo2', 'subt_short', 'speed'],
           'sharpen': ['race_pace', 'speed', 'subt_short'], 'taper': ['race_pace', 'speed'],
           'race': ['race_pace']},
    '10k': {'base': ['subt_short', 'subt_long', 'hills'], 'specific': ['subt_long', 'vo2', 'subt_short'],
            'sharpen': ['race_pace', 'subt_short', 'speed'], 'taper': ['race_pace', 'speed'],
            'race': ['race_pace']},
    'half': {'base': ['subt_short', 'subt_long', 'hills'], 'specific': ['subt_long', 'race_pace', 'subt_short'],
             'sharpen': ['race_pace', 'subt_short', 'vo2'], 'taper': ['race_pace', 'speed'],
             'race': ['race_pace']},
    'marathon': {'base': ['subt_short', 'subt_long', 'hills'], 'specific': ['subt_long', 'threshold', 'subt_short'],
                 'sharpen': ['subt_long', 'threshold', 'subt_short'], 'taper': ['race_pace', 'subt_short'],
                 'race': ['race_pace']},
}
RACE_ZONE = {'5k': 'I', '10k': 'T', 'half': 'HM', 'marathon': 'M'}


def _race_zone(zones, distance, goal_zones=None):
    """Loppfarten: målets fart under sharpen/taper om den finns, annars dagens."""
    key = RACE_ZONE.get(distance, 'T')
    src = goal_zones if goal_zones else zones
    return key, src[key]


def _work(spec, zones, distance, goal_zones=None):
    """Arbetsdelen: (text, km i zon, minuter i zon, zonnyckel)."""
    if spec['zone'] == 'RACE':
        key, z = _race_zone(zones, distance, goal_zones)
        fast, slow, mid = z['lowSec'], z['highSec'], z['paceSec']
        label = f"loppfart {fmt_pace(fast)}–{fmt_pace(slow)}/km"
        zkey = key
    else:
        zkey = spec['zone']
        fast, slow, mid = _zone(zones, zkey)
        label = f"{ZONE_NAMES[zkey]} {fmt_pace(fast)}–{fmt_pace(slow)}/km"
    reps = spec['reps']
    if 'meters' in spec:
        rep = f"{spec['meters']} m" if spec['meters'] < 1000 or spec['meters'] % 1000 else f"{spec['meters'] // 1000} km"
        km = reps * spec['meters'] / 1000
        minutes = km * mid / 60
        if zkey in ('R', 'I') and spec['meters'] <= 400:
            label += f" ({round(mid * spec['meters'] / 1000)} s per drag)"
    elif 'min' in spec:
        rep = f"{spec['min']} min"
        minutes = reps * spec['min']
        km = minutes * 60 / mid
    else:
        rep = f"{spec['sec']} s"
        minutes = reps * spec['sec'] / 60
        km = minutes * 60 / mid
        label = 'hårt men kontrollerat i uppförsbacke'
    head = f'{reps} × {rep}' if reps > 1 else rep
    rest = f", {spec['rest']}" if spec.get('rest') and reps > 1 else ''
    # Vila mellan dragen räknas som ungefär 150 m jogg per vila för km-summan.
    rest_km = 0.15 * (reps - 1) if spec.get('rest') else 0
    return f'{head} {label}{rest}', km, minutes, zkey, rest_km, head


def _cap_spec(spec, weekly_km, zones, distance, goal_zones=None):
    """Daniels per-pass-tak: färre drag tills arbetet ryms inom taket."""
    spec = dict(spec)
    zone = spec['zone']
    if zone in ('T', 'SUBT', 'RACE'):
        cap = 0.10 * weekly_km if zone == 'T' else 0.12 * weekly_km
    elif zone == 'I':
        cap = min(0.08 * weekly_km, 10)
    elif zone == 'R':
        cap = min(0.05 * weekly_km, 8)
    else:
        cap = None
    # Sub-tröskel är medvetet något långsammare än T och tillåts därför 12 %.
    # Loppfart på HM/M räknas som tröskelnära.
    while cap and spec['reps'] > 2 and _work(spec, zones, distance, goal_zones)[1] > cap:
        spec['reps'] -= 1
    return spec


def quality_session(kind, index, weekly_km, zones, distance, lthr=None, goal_zones=None,
                    easy_share=1.0):
    specs = LIBRARY[kind]
    spec = _cap_spec(specs[index % len(specs)], weekly_km * easy_share, zones, distance, goal_zones)
    text, km, minutes, zkey, rest_km, head = _work(spec, zones, distance, goal_zones)
    hr = hr_text(zkey if zkey in HR_SHARE else 'T', lthr)
    titles = {'subt_short': 'Sub-tröskel', 'subt_long': 'Långa tröskeldrag', 'threshold': 'Tröskel',
              'vo2': 'Intervaller', 'speed': 'Snabbhet', 'hills': 'Backar', 'race_pace': 'Loppfart'}
    session = {
        'kind': kind, 'type': 'run', 'title': f'{titles[kind]} {head}',
        'purpose': PURPOSE[kind], 'slot': None,
        'zone': zkey, 'qualityKm': round(km, 1), 'qualityMin': round(minutes, 2),
        'structure': [
            {'part': 'warmup'},
            {'part': 'work', 'text': text + (f' ({hr})' if hr else ''),
             'km': km, 'minutes': minutes, 'reps': spec['reps'], 'zone': spec['zone'],
             'restKm': rest_km, 'restMinutes': spec['reps'] - 1 if spec.get('rest') else 0},
            {'part': 'cooldown'},
        ],
    }
    _set_quality_padding(session, zones, WARMUP_KM, COOLDOWN_KM)
    return session


def _set_quality_padding(session, zones, warmup_km, cooldown_km):
    """Håll text, struktur, distans och tid överens när lugna delar kortas."""
    warm, work, cool = session['structure']
    warm.update(km=warmup_km, text=f'{warmup_km:g} km uppvärmning {pace_text(zones, "E")}'
                + (' + 4 stegringar' if session['zone'] in ('I', 'R', 'HM', 'T') else ''))
    cool.update(km=cooldown_km, text=f'{cooldown_km:g} km nedjogg')
    session['detail'] = ' · '.join(part['text'] for part in session['structure'])
    session['km'] = round(warmup_km + work['km'] + work['restKm'] + cooldown_km, 1)
    # Vilorna räknas som cirka en minut vardera, inte en gång till i E-fart.
    session['minutes'] = round((warmup_km + cooldown_km) * zones['E']['paceSec'] / 60
                               + work['minutes'] + work['restMinutes'], 2)


def long_run(km, zones, phase, index, distance, lthr=None, goal_zones=None):
    e = pace_text(zones, 'E')
    hr = hr_text('E', lthr)
    # Avrunda nedåt så att varken km- eller 150-minuterstaket överskrids.
    km = floor(km * 10 + 1e-9) / 10
    finish = 0
    finish_pace = 0
    if phase in ('specific', 'sharpen') and distance in ('half', 'marathon'):
        finish = min(8 if distance == 'half' else 14, 3 + 2 * index, int(km * 0.4))
    if phase == 'base' and index % 2 == 1:
        finish = min(3, km)
        finish_pace = (zones['E']['paceSec'] + zones['M']['paceSec']) / 2
        detail = f'{km:g} km {e}{f" ({hr})" if hr else ""}, de sista {finish:g} km progressivt mot M-fart {pace_text(zones, "M")}'
        kind, title = 'long', f'Långpass {km} km progressivt'
    elif finish:
        key, z = _race_zone(zones, distance, goal_zones)
        finish_pace = z['paceSec']
        detail = (f'{km - finish} km {e} och sedan {finish} km i loppfart '
                  f'{fmt_pace(z["lowSec"])}–{fmt_pace(z["highSec"])}/km')
        kind, title = 'long_mp', f'Långpass {km} km, {finish} km loppfart'
    else:
        detail = f'{km} km {e}{f" ({hr})" if hr else ""}, jämnt och lugnt'
        kind, title = 'long', f'Långpass {km} km'
    return {'kind': kind, 'type': 'run', 'title': title, 'detail': detail, 'purpose': PURPOSE[kind],
            'km': km, 'zone': 'E', 'qualityKm': finish,
            'qualityMin': round(finish * finish_pace / 60, 2), 'slot': None,
            'minutes': round(((km - finish) * zones['E']['paceSec'] + finish * finish_pace) / 60, 2),
            'structure': [{'part': 'work', 'text': detail}]}


def easy_run(km, zones, lthr=None, strides=False, recovery=False):
    e = pace_text(zones, 'E')
    hr = hr_text('E', lthr)
    km = round(km, 1)
    km_txt = f'{km:g}'.replace('.', ',')
    detail = f'{km_txt} km {e}{f" ({hr})" if hr else ""}' + (f' · {STRIDES}' if strides else '')
    title = f'{km_txt} km ' + ('återhämtning' if recovery else 'lugnt') + (' + stegringar' if strides else '')
    kind = 'recovery' if recovery else 'easy'
    return {'kind': kind, 'type': 'easy', 'title': title, 'detail': detail, 'purpose': PURPOSE[kind],
            'km': km, 'zone': 'E', 'qualityKm': 0, 'qualityMin': 0,
            'minutes': round(km * zones['E']['paceSec'] / 60, 2), 'slot': None,
            'structure': [{'part': 'work', 'text': detail}]}


def run_days(profile):
    profile = profile or {}
    days = list(profile.get('preferredDays') or [])
    n = profile.get('sessionsPerWeek') or 0
    if not days:
        days = DEFAULT_DAYS[max(3, min(7, n or 5))]
    return sorted(set(days))


def _circ_gap(a, b):
    d = abs(a - b)
    return min(d, 7 - d)


def place_quality(days, long_day, count):
    """Välj kvalitetsdagar som ligger så långt som möjligt från varandra och
    från långpasset (≥ 48 h när det går). Tisdag och torsdag föredras vid lika."""
    pool = [d for d in days if d != long_day]
    count = max(0, min(count, len(pool)))
    if not count:
        return []
    best, best_key = None, None
    for combo in combinations(pool, count):
        points = sorted(combo + ((long_day,) if long_day is not None else ()))
        gaps = [_circ_gap(a, b) for a, b in combinations(points, 2)]
        min_gap = min(gaps) if gaps else 7
        # Inget kvalitetspass dagen före långpasset om det går att undvika.
        before_long = sum(1 for d in combo if long_day is not None and (d + 1) % 7 == long_day)
        prefer = sum(1 for d in combo if d in (1, 3))
        q_adjacent = sum(1 for a, b in combinations(combo, 2) if _circ_gap(a, b) == 1)
        key = (-q_adjacent, min_gap, -before_long, prefer, -sum(combo))
        if best_key is None or key > best_key:
            best, best_key = combo, key
    return sorted(best)


def build_week(frame, zones, profile=None, distance='half', lthr=None, goal_zones=None,
               from_dow=0, race_dow=6, race_label=None):
    """Alla pass i en vecka. `frame` är en vecka från season_plan.build().

    Returnerar {'sessions': [...], 'summary': {...}, 'checks': [...]}.
    Dagar före `from_dow` planeras inte (pågående vecka)."""
    profile = profile or {}
    level = profile.get('experience') or ''
    phase = frame['phase']
    recovery = frame.get('recovery')
    target = float(frame['targetKm'])
    days = run_days(profile)
    long_day = profile.get('longRunDay')
    if long_day not in days:
        long_day = 6 if 6 in days else days[-1]
    if phase == 'race':
        long_day = race_dow
        if long_day not in days:
            days = sorted(set(days + [long_day]))

    n_quality = QUALITY_BY_LEVEL.get(level, 2)
    if len(days) <= 3:
        n_quality = min(n_quality, 1)
    elif len(days) == 4:
        n_quality = min(n_quality, 2)
    if recovery or phase == 'taper':
        n_quality = min(n_quality, 2 if level == 'competitive' else 1)
    if phase == 'race':
        n_quality = 1
    double = (level == 'competitive' and (profile.get('sessionsPerWeek') or 0) >= 9
              and phase in ('base', 'specific') and not recovery)

    q_days = place_quality(days, long_day, n_quality)
    # Vissa önskade dagkombinationer rymmer inte tre åtskilda kvalitetsdagar.
    while len(days) >= 5 and any(b - a < 2 for a, b in zip(q_days, q_days[1:])):
        q_days = place_quality(days, long_day, len(q_days) - 1)
    kinds = PHASE_QUALITY.get(distance, PHASE_QUALITY['half']).get(phase, ['subt_short'])
    idx = frame.get('indexInPhase', frame['week'])
    sessions = []

    # Kvalitetspass: tröskel först (viktigast), sedan övriga i fasens ordning.
    used_km = 0.0
    for i, d in enumerate(q_days):
        kind = kinds[i % len(kinds)]
        if recovery:
            kind = 'subt_short'
        if double and i == 0 and not kind.startswith('subt'):
            kind = 'subt_short'
        s = quality_session(kind, idx + i, target, zones, distance, lthr, goal_zones,
                            easy_share=0.8 if recovery else 1.0)
        if phase == 'race':
            s['title'] = 'Kort fartpass: ' + s['title']
        s['dow'] = d
        sessions.append(s)
        used_km += s['km']
        if double and kind.startswith('subt') and i == 0:
            pm = quality_session('subt_long' if kind == 'subt_short' else 'subt_short', idx, target,
                                 zones, distance, lthr, goal_zones)
            pm['title'] = 'Dubbeltröskel PM: ' + pm['title']
            s['title'] = 'Dubbeltröskel AM: ' + s['title']
            pm['purpose'] = ('Andra sub-tröskelpasset samma dag (norska dubbeltröskeln). Två '
                             'kontrollerade pass ger mer tid vid tröskeln än ett hårt.')
            pm['dow'] = d
            s['slot'] = 'am'
            pm['slot'] = 'pm'
            pm['double'] = True
            sessions.append(pm)
            used_km += pm['km']

    # Långpass eller lopp.
    if phase == 'race':
        race_km = {'5k': 5.0, '10k': 10.0, 'half': 21.1, 'marathon': 42.2}.get(distance, 21.1)
        key, z = _race_zone(zones, distance, goal_zones)
        sessions.append({'kind': 'race', 'type': 'race', 'dow': long_day,
                         'title': race_label or 'Loppet',
                         'detail': (f'Uppvärmning 2 km lugnt + 3 stegringar. Starta kontrollerat i '
                                    f'{fmt_pace(z["lowSec"])}–{fmt_pace(z["highSec"])}/km och öka efter halva '
                                    'om det känns bra.'),
                         'purpose': PURPOSE['race'], 'km': race_km + 2, 'zone': key,
                         'qualityKm': race_km, 'qualityMin': round(race_km * z['paceSec'] / 60, 2),
                         'minutes': round((2 * zones['E']['paceSec'] + race_km * z['paceSec']) / 60, 2),
                         'slot': None, 'structure': []})
        used_km += race_km + 2
    else:
        long_km = min(float(frame.get('longRunKm') or target * 0.28), target * 0.30,
                      150 * 60 / _zone(zones, 'E')[2])
        lr = long_run(long_km, zones, phase, idx, distance, lthr, goal_zones)
        lr['dow'] = long_day
        sessions.append(lr)
        used_km += lr['km']

    # Lätta dagar fyller resten.
    taken = {s['dow'] for s in sessions}
    easy_days = [d for d in days if d not in taken]
    remaining = max(0.0, target - used_km)
    min_easy = MIN_EASY_KM_SHORT if level in ('competitive', 'experienced') else MIN_EASY_KM
    if easy_days:
        if remaining < len(easy_days) * min_easy:
            # Behåll löpfrekvensen: korta först lugna delar av kvalitetspassen.
            for s in sessions:
                if s['kind'] in LIBRARY:
                    previous_km = s['km']
                    _set_quality_padding(s, zones, 2.0, 1.5)
                    used_km -= previous_km - s['km']
            remaining = max(0.0, target - used_km)
        while remaining / len(easy_days) < min_easy and len(easy_days) > 1:
            # För få km för alla lätta dagar: dagen efter ett hårt pass blir vilodag.
            hard_next = [d for d in easy_days if (d - 1) % 7 in taken]
            easy_days.remove(hard_next[0] if hard_next else easy_days[-1])
        per = remaining / len(easy_days) if easy_days else 0
        cap = max(min_easy, (sessions[-1]['km'] if phase != 'race' else 12) * 0.75)
        strides_left = 2 if phase in ('base', 'specific', 'sharpen') and not recovery else 1
        for d in easy_days:
            km = max(min_easy, min(per, cap))
            after_hard = (d - 1) % 7 in taken
            strides = (not after_hard and strides_left > 0 and (d + 1) % 7 not in taken)
            if strides:
                strides_left -= 1
            s = easy_run(km, zones, lthr, strides=strides, recovery=after_hard and km <= 8)
            s['dow'] = d
            sessions.append(s)

    if phase != 'race':
        # Vid få löpdagar kan veckan bli kortare än ramen. Taket gäller även
        # den faktiskt planerade veckan, inte bara targetKm.
        other_km = sum(s['km'] for s in sessions if s is not lr)
        if lr['km'] > other_km * 0.30 / 0.70:
            shorter = long_run(other_km * 0.30 / 0.70, zones, phase, idx, distance, lthr, goal_zones)
            lr.update(shorter)

    # Styrka på kvalitetsdagar (hårt hårt, lätt lätt).
    strength = frame.get('strength') or {}
    lifts = int(strength.get('sessions') or 0)
    lift_days = [d for d in q_days if d != long_day][:lifts]
    if lifts > len(lift_days):
        lift_days += [d for d in easy_days if d not in lift_days][:lifts - len(lift_days)]
    for d in lift_days:
        sessions.append({'kind': 'strength', 'type': 'lift', 'dow': d,
                         'title': 'Styrka efter löpningen',
                         'detail': strength.get('text') or 'Tunga baslyft och enbensövningar.',
                         'purpose': PURPOSE['strength'], 'km': 0, 'zone': None,
                         'minutes': 0, 'slot': None,
                         'qualityKm': 0, 'qualityMin': 0, 'structure': []})

    sessions = [s for s in sessions if s['dow'] >= from_dow]
    sessions.sort(key=lambda s: (s['dow'], s['type'] == 'lift', s.get('double', False)))
    for s in sessions:
        s['day'] = DAY_NAMES[s['dow']]
    summary, checks = check_week(sessions, target, phase)
    return {'sessions': sessions, 'summary': summary, 'checks': checks}


def check_week(sessions, target_km, phase):
    """Reglerna som gäller för veckan, och om planen följer dem."""
    runs = [s for s in sessions if s['type'] in ('run', 'easy', 'race')]
    km = sum(s['km'] for s in runs)
    quality_km = sum(s.get('qualityKm') or 0 for s in runs)
    minutes = sum(s['minutes'] for s in runs)
    quality_minutes = sum(s.get('qualityMin') or 0 for s in runs)
    hard_days = sorted({s['dow'] for s in runs if s['kind'] not in ('easy', 'recovery', 'long', 'long_mp')})
    long = max((s for s in runs if s['kind'] in ('long', 'long_mp')), key=lambda s: s['km'], default=None)
    easy_share = 1 - quality_km / km if km else 1
    easy_time_share = 1 - quality_minutes / minutes if minutes else 1
    gaps = [b - a for a, b in zip(hard_days, hard_days[1:])]
    checks = [
        {'rule': '80/20-fördelning', 'source': 'Seiler',
         'ok': easy_time_share >= 0.75 or phase in ('race', 'taper'),
         'text': f'{round(easy_share * 100)} % av veckans km går lugnt, inklusive uppvärmning '
                 f'och vila; {round(easy_time_share * 100)} % av tiden (mål omkring 80 %).'},
        {'rule': 'Högst tre hårda dagar', 'source': 'Seiler',
         'ok': len(hard_days) <= 3,
         'text': f'{len(hard_days)} kvalitetsdag{"ar" if len(hard_days) != 1 else ""} utöver långpasset.'},
        {'rule': 'Vila mellan hårda pass', 'source': 'Seiler',
         'ok': all(g >= 2 for g in gaps),
         'text': 'Minst en lätt dag mellan kvalitetspassen.' if all(g >= 2 for g in gaps)
                 else 'Två kvalitetsdagar ligger i rad. Välj fler träningsdagar i profilen.'},
    ]
    if long:
        share = long['km'] / km if km else 0
        checks.append({'rule': 'Långpass ≤ 30 %', 'source': 'Daniels', 'ok': share <= 0.30 + 1e-9,
                       'text': f'Långpasset är {round(share * 100)} % av veckan.'})
    if target_km:
        dev = km / target_km - 1
        checks.append({'rule': 'Veckans volym', 'source': 'Säsongsplanen', 'ok': abs(dev) <= 0.12,
                       'text': f'{round(km)} km planerat mot ramens {round(target_km)} km.'})
    summary = {'km': round(km, 1), 'qualityKm': round(quality_km, 1),
               'easyShare': round(easy_share, 2), 'hardDays': len(hard_days),
               'easyTimeShare': round(easy_time_share, 2),
               'runDays': len({s['dow'] for s in runs}),
               'longKm': long['km'] if long else 0, 'phase': phase}
    return summary, checks


def library(zones, lthr=None, weekly_km=60, distance='half'):
    """Passbanken: alla kvalitetspass med dagens tempon och varför de finns."""
    out = []
    for kind in ('subt_short', 'subt_long', 'threshold', 'vo2', 'hills', 'speed', 'race_pace'):
        items = []
        for i in range(len(LIBRARY[kind])):
            s = quality_session(kind, i, weekly_km, zones, distance, lthr)
            items.append({'title': s['title'], 'detail': s['detail'], 'km': s['km'],
                          'qualityMin': s['qualityMin']})
        out.append({'kind': kind, 'title': items[0]['title'].split(' ')[0] if kind != 'subt_long'
                    else 'Långa tröskeldrag', 'purpose': PURPOSE[kind], 'sessions': items})
    names = {'subt_short': 'Sub-tröskel, korta drag', 'subt_long': 'Sub-tröskel, långa drag',
             'threshold': 'Tröskel (Daniels T)', 'vo2': 'VO2max-intervaller', 'hills': 'Backintervaller',
             'speed': 'Snabbhet (R)', 'race_pace': 'Loppfart'}
    for group in out:
        group['title'] = names[group['kind']]
    return out


def hr_zones(lthr=None, max_hr=None):
    """Pulsintervall per tempozon, från tröskelpulsen (Friel) eller maxpulsen."""
    if not lthr and max_hr:
        lthr = round(max_hr * 0.9)
    if not lthr:
        return None
    out = {}
    for key, (low, high) in HR_SHARE.items():
        if low is None:
            continue
        out[key] = {'low': round(lthr * low), 'high': round(lthr * high)}
    out['E']['low'] = None
    return {'lthr': lthr, 'zones': out}
