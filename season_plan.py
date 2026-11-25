"""The season from today to the goal race: phases, weekly volume and key sessions.

Pure and deterministic. The detailed sessions are still planned one week at a
time (by the athlete or the coach), but every one of those weeks now answers to
a frame: which phase it is, how many kilometres it should hold, how long the
long run is and which two or three sessions carry the week.

Phases are counted backwards from the race week, so a plan that starts late
simply loses its base phase first. Paces come from the athlete's current VDOT
zones (vdot.paces), never from fixed numbers, so the key sessions sharpen as the
fitness does. The base volume is frozen when the season is created: if it were
read from the last four weeks every time, the targets would drift with every
missed run and stop being targets.
"""

from datetime import date, timedelta

import plan_calendar

RACE_KM = {'5k': 5.0, '10k': 10.0, 'half': 21.0975, 'marathon': 42.195}
RACE_LABELS = {'5k': '5 km', '10k': '10 km', 'half': 'Halvmaraton', 'marathon': 'Maraton'}

# Weeks per phase, counted back from the race week (which is its own phase).
PHASE_LENGTHS = {
    '5k': {'taper': 0, 'sharpen': 2, 'specific': 4},
    '10k': {'taper': 1, 'sharpen': 2, 'specific': 4},
    'half': {'taper': 1, 'sharpen': 2, 'specific': 5},
    'marathon': {'taper': 2, 'sharpen': 2, 'specific': 6},
}
LONG_RUN_CAP = {'5k': 14, '10k': 17, 'half': 21, 'marathon': 32}

PHASES = {
    'base': {'label': 'Bas + tröskel', 'color': 'base',
             'aim': 'Bygg volym och tröskel. Långpassen växer, farten hålls kontrollerad.'},
    'specific': {'label': 'Loppspecifikt', 'color': 'specific',
                 'aim': 'Längre tröskeldrag och loppfart i långpassen. Volymen når toppen.'},
    'sharpen': {'label': 'Loppfart', 'color': 'sharpen',
                'aim': 'Mycket loppfart, volymen ligger kvar men toppas inte mer.'},
    'taper': {'label': 'Nedtrappning', 'color': 'taper',
              'aim': 'Kortare men inte långsammare: behåll fartinslagen, sänk volymen.'},
    'race': {'label': 'Loppvecka', 'color': 'race',
             'aim': 'Vila in formen. Ett kort fartpass tidigt i veckan, sedan lugnt.'},
}

# Styrka följer löpningen: tyngst när löpningen är som minst specifik, sedan
# underhåll. Nyckeln läses av strength_program.
STRENGTH_BY_PHASE = {
    'base': {'sessions': 2, 'load': 'heavy',
             'text': '2 pass/vecka: tunga baslyft 3–5 reps, enbensövningar, vader och fot.'},
    'specific': {'sessions': 2, 'load': 'heavy_reduced',
                 'text': '2 pass/vecka, färre set: tungt men aldrig till failure.'},
    'sharpen': {'sessions': 1, 'load': 'maintain',
                'text': '1 pass/vecka: underhåll och explosivt, inga nya maxvikter.'},
    'taper': {'sessions': 1, 'load': 'maintain_light',
              'text': '1 kort pass tidigt i veckan, halva seten.'},
    'race': {'sessions': 0, 'load': 'none', 'text': 'Ingen styrka loppveckan.'},
    'recovery': {'sessions': 1, 'load': 'light',
                 'text': '1 lätt pass: samma övningar, lägre vikt, inga tunga set.'},
}

RECOVERY_EVERY = 4
RECOVERY_FACTOR = 0.78
HOLIDAY_FACTOR = 0.9
TAPER_FACTORS = {1: [0.75], 2: [0.8, 0.62]}
RACE_WEEK_FACTOR = 0.5


def _is_holiday_week(monday):
    """Julen: veckor som rör 20 dec–3 jan blir lite lugnare."""
    for offset in range(7):
        d = monday + timedelta(days=offset)
        if (d.month == 12 and d.day >= 20) or (d.month == 1 and d.day <= 3):
            return True
    return False


def peak_km(base_km, distance):
    base_km = max(15.0, float(base_km or 0))
    growth = {'5k': 1.12, '10k': 1.15, 'half': 1.2, 'marathon': 1.3}[distance]
    cap = {'5k': 10, '10k': 12, 'half': 14, 'marathon': 22}[distance]
    return min(base_km * growth, base_km + cap)


def _phase_for(weeks_to_race, lengths):
    if weeks_to_race == 0:
        return 'race'
    if weeks_to_race <= lengths['taper']:
        return 'taper'
    if weeks_to_race <= lengths['taper'] + lengths['sharpen']:
        return 'sharpen'
    if weeks_to_race <= lengths['taper'] + lengths['sharpen'] + lengths['specific']:
        return 'specific'
    return 'base'


def _fmt(sec):
    sec = int(round(sec))
    return f'{sec // 60}:{sec % 60:02d}'


def _race_time_text(seconds):
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    return f'{h}:{rest // 60:02d}:{rest % 60:02d}' if h else f'{rest // 60}:{rest % 60:02d}'


def key_sessions(phase, index_in_phase, long_km, zones, distance, recovery=False,
                 holiday=False, goal_zones=None, race_label=None):
    """Two or three sessions that carry the week, with paces from the zones.

    `zones` is vdot.paces(); without it the sessions are written by zone letter
    only. `goal_zones` gives the goal's race pace for the sharpening phase."""
    def z(key, field='pace'):
        if not zones:
            return key
        zone = zones[key]
        return zone['text'] if field == 'text' else zone['pace'].replace('/km', '')

    E = f"E {z('E', 'text')}" if zones else 'E'
    T = f"T {z('T')}" if zones else 'T'
    I = f"I {z('I')}" if zones else 'I'
    R = f"R {z('R')}" if zones else 'R'
    race_key = 'HM' if distance in ('half', 'marathon') else 'I' if distance == '5k' else 'T'
    race_pace = z(race_key) if zones else race_key
    if goal_zones and phase in ('sharpen', 'taper', 'race'):
        race_pace = goal_zones[race_key]['pace'].replace('/km', '')
    RP = f'loppfart {race_pace}'
    i = index_in_phase

    if recovery:
        return [
            {'kind': 'threshold', 'title': 'Lätt tröskel', 'detail': f'2 km {E} · 5 × 3 min {T}, 1 min jogg · 2 km nedjogg'},
            {'kind': 'long', 'title': f'Långpass {long_km} km', 'detail': f'{long_km} km {E}, helt lugnt'},
        ]
    if holiday and phase == 'base':
        return [
            {'kind': 'threshold', 'title': 'Tröskel 3 × 10 min', 'detail': f'2 km {E} · 3 × 10 min {T}, 90 s jogg · 2 km {E}'},
            {'kind': 'long', 'title': f'Långpass {long_km} km', 'detail': f'{long_km} km {E}'},
        ]
    if phase == 'base':
        tempo = ['4 × 8 min', '5 × 8 min', '3 × 12 min', '4 × 10 min', '3 × 15 min'][i % 5]
        speed = ['8 × 400 m', '6 × 800 m', '5 × 1000 m', '10 × 300 m backe'][i % 4]
        speed_pace = R if '400' in speed or 'backe' in speed else I
        return [
            {'kind': 'threshold', 'title': f'Tröskel {tempo}', 'detail': f'2 km {E} · {tempo} {T}, 1 min jogg · 2 km {E}'},
            {'kind': 'interval', 'title': speed, 'detail': f'2,5 km {E} med 4 stegringar · {speed} {speed_pace}, jogg lika långt som draget · 2 km {E}'},
            {'kind': 'long', 'title': f'Långpass {long_km} km', 'detail': f'{long_km} km {E}' + (', sista 2 km lite piggare' if i % 2 else '')},
        ]
    if phase == 'specific':
        tempo = ['3 × 15 min', '2 × 20 min', '4 × 12 min', '2 × 25 min', '3 × 15 min'][i % 5]
        race_block = ['3 × 3 km', '4 × 3 km', '2 × 5 km', '3 × 4 km', '2 × 6 km'][i % 5]
        fast_finish = min(8, 3 + i)
        return [
            {'kind': 'threshold', 'title': f'Långa tröskeldrag {tempo}', 'detail': f'2 km {E} · {tempo} {T}, 2 min jogg · 2 km {E}'},
            {'kind': 'race', 'title': f'Loppfart {race_block}', 'detail': f'2,5 km {E} · {race_block} i {RP}, 2 min jogg · 2 km {E}'},
            {'kind': 'long', 'title': f'Långpass {long_km} km med fartdel', 'detail': f'{long_km} km: lugnt {E}, de sista {fast_finish} km i {RP}'},
        ]
    if phase == 'sharpen':
        race_block = ['3 × 4 km', '2 × 6 km'][i % 2]
        return [
            {'kind': 'race', 'title': f'Loppfart {race_block}', 'detail': f'2,5 km {E} · {race_block} i {RP}, 2–3 min jogg · 2 km {E}'},
            {'kind': 'interval', 'title': '5 × 1000 m', 'detail': f'2,5 km {E} · 5 × 1000 m {I}, 2 min jogg · 2 km {E}'},
            {'kind': 'long', 'title': f'Långpass {long_km} km', 'detail': f'{long_km} km {E}, 4 km i {RP} i mitten'},
        ]
    if phase == 'taper':
        return [
            {'kind': 'race', 'title': 'Loppfart 2 × 3 km', 'detail': f'2 km {E} · 2 × 3 km i {RP}, 3 min jogg · 2 km {E}'},
            {'kind': 'interval', 'title': '6 × 400 m', 'detail': f'2 km {E} · 6 × 400 m {R}, full vila · 2 km {E}'},
            {'kind': 'long', 'title': f'Långpass {long_km} km', 'detail': f'{long_km} km {E}'},
        ]
    return [
        {'kind': 'race', 'title': 'Kort fartpass tidigt i veckan', 'detail': f'2 km {E} · 3 × 1 km i {RP}, 2 min jogg · 2 km {E}'},
        {'kind': 'race', 'title': race_label or 'Loppet', 'detail': f'Starta kontrollerat i {RP}, öka efter halva om det känns bra.'},
    ]


def build(race_date, distance, base_km, base_long_km, start_week, zones=None,
          goal_zones=None, goal_seconds=None, race_name=None, peak_override=None):
    """Every week from `start_week` through the race week. `peak_override`
    is the athlete's own top weekly volume; it may not be below the base."""
    if distance not in RACE_KM:
        raise ValueError(f'unknown distance {distance!r}')
    race_week = plan_calendar.plan_week(race_date)
    if race_week < start_week:
        return []
    lengths = PHASE_LENGTHS[distance]
    peak = peak_km(base_km, distance)
    if peak_override:
        peak = max(float(base_km or 0), min(float(peak_override), float(base_km or 0) * 1.6))
    cap = LONG_RUN_CAP[distance]
    base_long = max(8.0, min(float(base_long_km or 0) or base_km * 0.28, cap))
    sharpen_start = race_week - lengths['taper'] - lengths['sharpen']

    holidays = {w for w in range(start_week, race_week + 1)
                if _is_holiday_week(plan_calendar.plan_monday(w))}
    recoveries = set()
    for k in range(1, 20):
        w = race_week - RECOVERY_EVERY * k
        if w <= start_week:
            break
        if _phase_for(race_week - w, lengths) not in ('base', 'specific'):
            continue
        # Julen är redan en lättare period; en avlastningsvecka direkt
        # efter (eller i) den vore två lugna veckor i rad.
        if w in holidays or (w - 1) in holidays:
            continue
        recoveries.add(w)
    # Volymen når toppen den sista vanliga uppbyggnadsveckan (inte en avlastningsvecka).
    top_week = max([w for w in range(start_week, sharpen_start) if w not in recoveries] or [start_week])
    build_weeks = max(1, top_week - start_week)

    race_label = f"{race_name or RACE_LABELS[distance]}" + (
        f' · mål {_race_time_text(goal_seconds)}' if goal_seconds else '')
    weeks = []
    phase_index = {}
    for w in range(start_week, race_week + 1):
        to_race = race_week - w
        phase = _phase_for(to_race, lengths)
        idx = phase_index.get(phase, 0)
        phase_index[phase] = idx + 1
        monday = plan_calendar.plan_monday(w)
        progress = min(1.0, (w - start_week) / build_weeks)
        if phase in ('base', 'specific'):
            km = base_km + (peak - base_km) * progress
            long_km = base_long + (cap - base_long) * progress
        elif phase == 'sharpen':
            km = peak * 0.95
            long_km = max(base_long, cap * 0.85)
        elif phase == 'taper':
            factors = TAPER_FACTORS.get(lengths['taper'], [0.75])
            km = peak * factors[min(len(factors) - 1, lengths['taper'] - to_race)]
            long_km = max(8.0, cap * 0.65)
        else:
            km = peak * RACE_WEEK_FACTOR
            long_km = RACE_KM[distance]
        recovery = w in recoveries
        holiday = w in holidays and phase in ('base', 'specific')
        if recovery:
            km *= RECOVERY_FACTOR
            long_km *= 0.75
        elif holiday:
            km *= HOLIDAY_FACTOR
        long_km = round(min(long_km, cap if phase != 'race' else RACE_KM[distance]))
        strength = STRENGTH_BY_PHASE['recovery' if recovery else phase]
        weeks.append({
            'week': w,
            'isoWeek': plan_calendar.iso_week_label(w),
            'monday': monday.isoformat(),
            'phase': phase,
            'phaseLabel': PHASES[phase]['label'],
            'aim': PHASES[phase]['aim'],
            'recovery': recovery,
            'holiday': holiday,
            'weeksToRace': to_race,
            'targetKm': int(round(km)),
            'longRunKm': long_km,
            'keySessions': key_sessions(phase, idx, long_km, zones, distance, recovery=recovery,
                                        holiday=holiday, goal_zones=goal_zones,
                                        race_label=race_label),
            'strength': strength,
        })
    return weeks


def week_status(target_km, actual_km, planned_km, is_past, is_current):
    """How a week went (or is going) against its target."""
    if not target_km:
        return None
    if is_past:
        ratio = (actual_km or 0) / target_km
        if ratio >= 0.9:
            return 'on_target' if ratio <= 1.12 else 'over'
        return 'under'
    if is_current:
        return 'on_target' if 0.85 <= (planned_km or 0) / target_km <= 1.12 else (
            'over' if (planned_km or 0) > target_km * 1.12 else 'under_planned')
    return None


def describe(weeks, current_week, horizon=3, today_week=None):
    """Compact English lines for the planning prompts: `current_week` and the
    next few. `today_week` (default `current_week`) counts the weeks left."""
    today_week = today_week or current_week
    rows = [w for w in weeks if current_week <= w['week'] < current_week + horizon]
    if not rows:
        return ''
    race = weeks[-1]
    lines = [f"SEASON PLAN (Trainyze's frame for every week until the race on "
             f"{(date.fromisoformat(race['monday']) + timedelta(days=6)).isoformat()}, "
             f"{race['week'] - today_week} weeks away). "
             'Plan each week inside its frame: hold the target km within about ±10 %, '
             'keep the long run at its length and build the week around the key sessions:']
    for w in rows:
        tag = ' (recovery week)' if w['recovery'] else ' (Christmas, a bit lighter)' if w['holiday'] else ''
        sessions = '; '.join(f"{s['title']}: {s['detail']}" for s in w['keySessions'])
        lines.append(f"  Plan week {w['week']} (calendar week {w['isoWeek']}, from {w['monday']}): "
                     f"{w['phaseLabel']}{tag} · target {w['targetKm']} km · long run {w['longRunKm']} km · "
                     f"key sessions: {sessions} · strength: {w['strength']['text']}")
    return '\n'.join(lines)
