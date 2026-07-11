import re
import unicodedata
from datetime import date


EXERCISES = {
    'squat': {
        'label': 'Kn\u00e4b\u00f6j',
        'aliases': ('knaboj', 'kn\u00e4b\u00f6j', 'squat', 'back squat'),
        'increment': 2.5,
        'mode': 'weight',
        'default': (3, 5),
    },
    'deadlift': {
        'label': 'Marklyft',
        'aliases': ('marklyft', 'deadlift', 'konventionella marklyft'),
        'increment': 2.5,
        'mode': 'weight',
        'default': (3, 5),
    },
    'rdl': {
        'label': 'RDL',
        'aliases': ('rdl', 'romanian deadlift', 'rumanska marklyft', 'rum\u00e4nska marklyft'),
        'increment': 2.5,
        'mode': 'weight',
        'default': (3, 8),
    },
    'bench_press': {
        'label': 'B\u00e4nkpress',
        'aliases': ('bankpress', 'b\u00e4nkpress', 'bench press', 'benchpress'),
        'increment': 2.5,
        'mode': 'weight',
        'default': (4, 6),
    },
    'overhead_press': {
        'label': 'Axelpress',
        'aliases': ('axelpress', 'overhead press', 'military press', 'shoulder press'),
        'increment': 1.0,
        'mode': 'weight',
        'default': (3, 6),
    },
    'lat_pulldown': {
        'label': 'Latsdrag',
        'aliases': ('latsdrag', 'lat pulldown', 'latpulldown'),
        'increment': 5.0,
        'mode': 'weight',
        'default': (3, 8),
    },
    'row': {
        'label': 'Rodd',
        'aliases': ('rodd', 'row', 'cable row', 'seated row', 'sittande rodd'),
        'increment': 5.0,
        'mode': 'weight',
        'default': (3, 8),
    },
    'leg_press': {
        'label': 'Benpress',
        'aliases': ('benpress', 'leg press'),
        'increment': 5.0,
        'mode': 'weight',
        'default': (3, 10),
    },
    'split_squat': {
        'label': 'Bulgariska utfall',
        'aliases': ('bulgariska utfall', 'bulgarska utfall', 'bulgariska', 'bulgarska', 'split squat'),
        'increment': 2.0,
        'mode': 'weight',
        'default': (3, 8),
    },
    'biceps_curl': {
        'label': 'Bicepscurl',
        'aliases': ('bicepscurl', 'biceps curl'),
        'increment': 0.5,
        'mode': 'weight',
        'default': (3, 10),
    },
    'triceps_press': {
        'label': 'Tricepspress',
        'aliases': ('tricepspress', 'triceps press', 'triceps pushdown', 'pushdown'),
        'increment': 2.5,
        'mode': 'weight',
        'default': (3, 10),
    },
    'plank': {
        'label': 'Plankan',
        'aliases': ('plankan', 'plank'),
        'increment': None,
        'mode': 'timed',
        'default': (3, 45),
    },
    'dips': {
        'label': 'Dips',
        'aliases': ('dips', 'dip'),
        'increment': None,
        'mode': 'bodyweight',
        'default': (3, 8),
    },
    'pull_up': {
        'label': 'Chins',
        'aliases': ('chins', 'chin ups', 'chin-up', 'pull ups', 'pull-up'),
        'increment': None,
        'mode': 'bodyweight',
        'default': (3, 6),
    },
    'dead_bug': {
        'label': 'Dead bug',
        'aliases': ('dead bug',),
        'increment': None,
        'mode': 'bodyweight',
        'default': (3, 12),
    },
    'box_jump': {
        'label': 'Boxjumps',
        'aliases': ('boxjumps', 'box jumps', 'box jump'),
        'increment': None,
        'mode': 'bodyweight',
        'default': (4, 6),
    },
    'calf_raise': {
        'label': 'Vadpress',
        'aliases': ('vadpress', 'vadbagar', 'vadb\u00e5gar', 'vadhopp', 'calf raise'),
        'increment': None,
        'mode': 'bodyweight',
        'default': (3, 12),
    },
    'back_extension': {
        'label': 'Ryggh\u00e4v',
        'aliases': ('rygghav', 'ryggh\u00e4v', 'back extension'),
        'increment': None,
        'mode': 'bodyweight',
        'default': (3, 12),
    },
}


def normalize_name(value):
    text = unicodedata.normalize('NFKD', str(value or '').casefold())
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace('\u00d7', 'x').replace('\u2013', '-').replace('\u2014', '-')
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


_NORMALIZED_ALIASES = {
    key: tuple(sorted({normalize_name(alias) for alias in spec['aliases']}, key=len, reverse=True))
    for key, spec in EXERCISES.items()
}


def canonical_exercise(value, partial=False):
    normalized = normalize_name(value)
    if not normalized:
        return None
    for key, aliases in _NORMALIZED_ALIASES.items():
        if normalized in aliases:
            return key
    if partial:
        for key, aliases in _NORMALIZED_ALIASES.items():
            for alias in aliases:
                if re.search(r'(?<![a-z0-9])' + re.escape(alias) + r'(?![a-z0-9])', normalized):
                    return key
    return None


def _parse_prescription(value):
    normalized = unicodedata.normalize('NFKD', str(value or '').casefold())
    normalized = ''.join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.replace('\u00d7', 'x').replace('\u2013', '-').replace('\u2014', '-')
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    match = re.search(r'(?P<sets>\d+)\s*x\s*(?P<low>\d+|max)(?:\s*-\s*(?P<high>\d+))?\s*(?P<unit>sek|sekunder|s)?', normalized)
    if not match:
        return None
    low = match.group('low')
    return {
        'sets': int(match.group('sets')),
        'reps': None if low == 'max' else int(low),
        'repsMax': int(match.group('high')) if match.group('high') else None,
        'repLabel': 'max' if low == 'max' else low,
        'unit': 'seconds' if match.group('unit') else None,
    }


def extract_strength_targets(detail):
    detail = str(detail or '')
    segments = [part.strip() for part in re.split(r'[,;\u00b7]', detail) if part.strip()]
    all_prescriptions = [p for p in (_parse_prescription(part) for part in segments) if p]
    global_prescription = all_prescriptions[0] if len(all_prescriptions) == 1 else None
    targets = []
    seen = set()

    for segment in segments:
        key = canonical_exercise(segment, partial=True)
        if not key or key in seen:
            continue
        spec = EXERCISES[key]
        parsed = _parse_prescription(segment) or global_prescription
        if not parsed:
            sets, reps = spec['default']
            parsed = {'sets': sets, 'reps': reps, 'repsMax': None, 'repLabel': str(reps), 'unit': None}
        if spec['mode'] == 'timed':
            parsed['unit'] = 'seconds'
        targets.append({'canonical': key, 'exercise': spec['label'], **parsed})
        seen.add(key)
    return targets


def _rep_count(value):
    match = re.search(r'\d+(?:[.,]\d+)?', str(value or ''))
    return float(match.group(0).replace(',', '.')) if match else None


def _sets_count(value):
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def _round_to_step(value, step):
    if not step:
        return None
    return round(round(float(value) / step) * step, 2)


def _format_weight(value):
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f'{number:.1f}'.replace('.', ',')


def _target_text(target, reps=None):
    rep_value = reps if reps is not None else target.get('reps')
    rep_label = target.get('repLabel', 'max') if rep_value is None else str(int(rep_value))
    suffix = ' sek' if target.get('unit') == 'seconds' else ''
    return f"{target['sets']}\u00d7{rep_label}{suffix}"


def _base_result(target, reps=None):
    return {
        'canonical': target['canonical'],
        'exercise': target['exercise'],
        'sets': target['sets'],
        'reps': reps if reps is not None else target.get('reps'),
        'unit': target.get('unit'),
        'weight': None,
        'lastDate': None,
        'lastSets': None,
        'lastReps': None,
        'lastWeight': None,
        'confidence': 'none',
        'reason': '',
        'prescription': '',
    }


def _entry_sort_key(entry):
    return (str(entry.get('date') or ''), int(entry.get('id') or 0))


def _filtered_history(history, before_date=None):
    result = []
    for entry in history or []:
        key = canonical_exercise(entry.get('exercise'))
        day = str(entry.get('date') or '')[:10]
        if not key or (before_date and day and day >= str(before_date)[:10]):
            continue
        copy = dict(entry)
        copy['canonical'] = key
        copy['date'] = day
        result.append(copy)
    return result


def _recommend_target(target, entries, before_date=None):
    spec = EXERCISES[target['canonical']]
    result = _base_result(target)
    target_text = _target_text(target)

    if spec['mode'] != 'weight':
        result['confidence'] = 'planned'
        result['reason'] = 'Set och reps f\u00f6ljer passets plan; ingen extern vikt beh\u00f6vs.'
        result['prescription'] = f"{target['exercise']} {target_text}"
        return result

    weighted = [
        entry for entry in entries
        if entry.get('weight') is not None and float(entry.get('weight') or 0) > 0 and _rep_count(entry.get('reps'))
    ]
    if not weighted:
        result['reason'] = 'Ingen j\u00e4mf\u00f6rbar vikt finns i historiken \u00e4nnu.'
        result['prescription'] = f"{target['exercise']} {target_text} \u00b7 v\u00e4lj vikt med 2 reps kvar"
        return result

    weighted.sort(key=_entry_sort_key)
    latest_entry = weighted[-1]
    latest_session_id = str(latest_entry.get('sessionId') or latest_entry.get('session_id') or '')
    if latest_session_id:
        latest = [
            entry for entry in weighted
            if str(entry.get('sessionId') or entry.get('session_id') or '') == latest_session_id
        ]
    else:
        latest = [entry for entry in weighted if entry.get('date') == latest_entry.get('date')]

    injury_pattern = re.compile(r'\b(ont|smarta|sm\u00e4rta|skada|skadad|injur|pain|strack|str\u00e4ck|problem)\b', re.I)
    has_injury_note = any(injury_pattern.search(normalize_name(entry.get('note'))) for entry in latest)

    top_weight = max(float(entry['weight']) for entry in latest)
    top_rows = [entry for entry in latest if abs(float(entry['weight']) - top_weight) < 0.01]
    top_sets = sum(_sets_count(entry.get('sets')) for entry in top_rows)
    expanded_reps = []
    for entry in top_rows:
        expanded_reps.extend([_rep_count(entry.get('reps'))] * _sets_count(entry.get('sets')))
    top_min_reps = min(expanded_reps)
    top_avg_reps = sum(expanded_reps) / len(expanded_reps)
    all_total_reps = sum(_sets_count(entry.get('sets')) * _rep_count(entry.get('reps')) for entry in latest)

    result.update({
        'lastDate': latest_entry.get('date'),
        'lastSets': top_sets,
        'lastReps': int(top_min_reps) if float(top_min_reps).is_integer() else top_min_reps,
        'lastWeight': top_weight,
        'confidence': 'high',
    })

    if has_injury_note:
        result['confidence'] = 'caution'
        result['reason'] = 'Senaste loggen n\u00e4mner sm\u00e4rta eller problem, s\u00e5 vikten h\u00f6js inte automatiskt.'
        result['prescription'] = f"{target['exercise']} {target_text} \u00b7 l\u00e4tt testvikt, avbryt vid sm\u00e4rta"
        return result

    target_reps = target.get('reps')
    if target_reps is None:
        result['reason'] = 'Passet anger maxreps; anv\u00e4nd kontrollerad teknik och l\u00e4mna 1-2 reps i reserv.'
        result['prescription'] = f"{target['exercise']} {target_text}"
        return result

    target_reps = int(target_reps)
    target_sets = int(target['sets'])
    increment = float(spec['increment'])

    if target.get('repsMax'):
        high = int(target['repsMax'])
        if top_sets >= target_sets and top_min_reps >= high:
            recommended_reps = target_reps
            recommended_weight = top_weight + increment
            reason = f'Alla set n\u00e5dde {high} reps; h\u00f6j vikten ett steg och b\u00f6rja om p\u00e5 {target_reps} reps.'
        elif top_sets >= target_sets and top_min_reps >= target_reps:
            recommended_reps = min(high, int(top_min_reps) + 1)
            recommended_weight = top_weight
            reason = 'Beh\u00e5ll vikten och h\u00f6j en rep per set innan n\u00e4sta vikt\u00f6kning.'
        else:
            recommended_reps = target_reps
            recommended_weight = top_weight
            reason = 'Beh\u00e5ll vikten tills den nedre delen av repsintervallet klaras i alla set.'
    else:
        recommended_reps = target_reps
        target_total_reps = target_sets * target_reps

        session_best_e1rm = {}
        for entry in weighted:
            session_key = str(entry.get('sessionId') or entry.get('session_id') or entry.get('date'))
            e1rm = float(entry['weight']) * (1 + _rep_count(entry.get('reps')) / 30)
            session_best_e1rm[session_key] = max(session_best_e1rm.get(session_key, 0), e1rm)
        recent_session_keys = []
        for entry in reversed(weighted):
            session_key = str(entry.get('sessionId') or entry.get('session_id') or entry.get('date'))
            if session_key not in recent_session_keys:
                recent_session_keys.append(session_key)
            if len(recent_session_keys) == 3:
                break
        latest_e1rm = max(float(entry['weight']) * (1 + _rep_count(entry.get('reps')) / 30) for entry in latest)
        recent_best = max(session_best_e1rm[key] for key in recent_session_keys)
        reference_e1rm = min(recent_best, latest_e1rm * 1.05)
        reserve_reps = 2 + max(0, target_sets - 3) * 0.5
        estimated_weight = _round_to_step(reference_e1rm / (1 + (target_reps + reserve_reps) / 30), increment)

        if top_sets >= target_sets and top_min_reps >= target_reps:
            recommended_weight = top_weight + increment
            reason = f'Du klarade minst {target_reps} reps i alla {target_sets} set; h\u00f6j {increment:g} kg.'
        elif target_reps <= top_min_reps and target_total_reps <= all_total_reps * 1.1:
            if target_reps <= top_min_reps - 2 or target_total_reps <= all_total_reps * 0.9:
                recommended_weight = top_weight + increment
                reason = 'Det nya repm\u00e5let \u00e4r l\u00e4gre \u00e4n senast, s\u00e5 vikten kan h\u00f6jas ett kontrollerat steg.'
            else:
                recommended_weight = top_weight
                reason = 'Beh\u00e5ll vikten och bygg progression genom ett extra arbetsset.'
        elif top_sets >= target_sets and top_avg_reps < target_reps:
            recommended_weight = top_weight
            reason = 'Beh\u00e5ll vikten och klara samtliga planerade reps innan n\u00e4sta h\u00f6jning.'
        else:
            recommended_weight = estimated_weight
            if target_reps >= top_avg_reps:
                recommended_weight = min(recommended_weight, top_weight)
            recommended_weight = max(increment, recommended_weight)
            reason = 'Vikten anpassas till fler set/reps och cirka 2 reps i reserv.'

    if before_date and result['lastDate']:
        try:
            gap = (date.fromisoformat(str(before_date)[:10]) - date.fromisoformat(str(result['lastDate'])[:10])).days
        except ValueError:
            gap = 0
        if gap > 60:
            recommended_weight = min(recommended_weight, _round_to_step(top_weight * 0.9, increment))
            reason = f'Det var {gap} dagar sedan senaste loggen; starta cirka 10% l\u00e4ttare och bygg upp igen.'
            result['confidence'] = 'medium'

    recommended_weight = _round_to_step(recommended_weight, increment)
    result['reps'] = recommended_reps
    result['weight'] = recommended_weight
    result['reason'] = reason
    result['prescription'] = (
        f"{target['exercise']} {_target_text(target, recommended_reps)} @ {_format_weight(recommended_weight)} kg"
    )
    return result


def build_strength_recommendations(detail, history, before_date=None):
    filtered = _filtered_history(history, before_date)
    by_exercise = {}
    for entry in filtered:
        by_exercise.setdefault(entry['canonical'], []).append(entry)
    return [
        _recommend_target(target, by_exercise.get(target['canonical'], []), before_date)
        for target in extract_strength_targets(detail)
    ]


def build_default_recommendations(history, before_date=None, limit=12):
    filtered = _filtered_history(history, before_date)
    by_exercise = {}
    for entry in filtered:
        by_exercise.setdefault(entry['canonical'], []).append(entry)
    ordered = sorted(by_exercise, key=lambda key: max(_entry_sort_key(e) for e in by_exercise[key]), reverse=True)
    recommendations = []
    for key in ordered:
        spec = EXERCISES[key]
        sets, reps = spec['default']
        target = {
            'canonical': key,
            'exercise': spec['label'],
            'sets': sets,
            'reps': reps,
            'repsMax': None,
            'repLabel': str(reps),
            'unit': 'seconds' if spec['mode'] == 'timed' else None,
        }
        recommendations.append(_recommend_target(target, by_exercise[key], before_date))
        if len(recommendations) >= limit:
            break
    return recommendations


def recommendation_summary(recommendations):
    return ' \u00b7 '.join(item['prescription'] for item in recommendations if item.get('prescription'))
