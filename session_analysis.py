"""Execution analysis for completed sessions.

The plan stores its intent as free text in `plan_sessions.detail`
("Z2 · 4:50–5:15/km", "5×1000m @ 3:30/km", "Knäböj 4×8 · 70%"). This module
parses that intent, pulls the matching evidence out of the Garmin activity
(laps, pace, heart rate) or the strength log, and returns a structured verdict
so the coach can say *how* a session went instead of just *that* it happened.

Nothing here touches the database or the network — callers pass in the rows
they already loaded, which keeps the whole module unit-testable.
"""

import re

# ── Pace/target parsing ───────────────────────────────────────────────────────

# Plan text uses both hyphen and en dash for ranges, and always marks pace
# with "/km" — durations like "20–25 min" must never be read as a pace.
_DASH = r'[–—-]'
_PACE_RANGE_RE = re.compile(
    rf'(\d{{1,2}}):(\d{{2}})\s*{_DASH}\s*(\d{{1,2}}):(\d{{2}})\s*/\s*km', re.I)
_PACE_SINGLE_RE = re.compile(r'(\d{1,2}):(\d{2})\s*/\s*km', re.I)
_ZONE_RE = re.compile(r'\bZ([1-5])\b', re.I)
_REPS_RE = re.compile(r'(\d{1,2})\s*[×x*]\s*(\d{2,5})\s*(m|km)\b', re.I)
# Kvalitetspass anges lika ofta i tid som i meter ("6×6 min tröskel").
_REPS_TIME_RE = re.compile(r'(\d{1,2})\s*[×x*]\s*(\d{1,3})\s*(min|sek|s)\b', re.I)

# Session kinds we can judge differently. `easy` is the one where running too
# fast is the actual mistake, so it gets its own bucket.
EASY_WORDS = ('z2', 'lugn', 'lätt', 'återhämt', 'jogg', 'aktiv återhämtning')
INTERVAL_WORDS = ('interval', 'intervall', 'track', 'fartlek', 'repeat', 'x400', 'x1000')
THRESHOLD_WORDS = ('tröskel', 'threshold', 'tempo')
LONG_WORDS = ('långpass', 'long run', 'distans')


def pace_to_seconds(minutes, seconds):
    return int(minutes) * 60 + int(seconds)


def format_pace(seconds_per_km):
    """Seconds per km -> 'm:ss/km'."""
    if not seconds_per_km or seconds_per_km <= 0:
        return None
    total = int(round(seconds_per_km))
    return f"{total // 60}:{total % 60:02d}/km"


def speed_to_pace_seconds(speed_ms):
    """Garmin reports m/s; the plan speaks in seconds per km."""
    if not speed_ms or speed_ms <= 0:
        return None
    return 1000.0 / speed_ms


def parse_pace_target(detail):
    """Extract the intended pace band from a plan session's detail text.

    Returns {'lowSec', 'highSec', 'text'} where low is the *fast* end, or None.
    A single pace ("@ 3:30/km") becomes a band with both ends equal.
    """
    text = str(detail or '')
    match = _PACE_RANGE_RE.search(text)
    if match:
        first = pace_to_seconds(match.group(1), match.group(2))
        second = pace_to_seconds(match.group(3), match.group(4))
        low, high = min(first, second), max(first, second)
        return {'lowSec': low, 'highSec': high,
                'text': f"{format_pace(low)}–{format_pace(high)}"}
    match = _PACE_SINGLE_RE.search(text)
    if match:
        value = pace_to_seconds(match.group(1), match.group(2))
        return {'lowSec': value, 'highSec': value, 'text': format_pace(value)}
    return None


def parse_rep_target(detail):
    """Extract a rep prescription from plan text.

    Handles both distance reps ('5×1000m @ 3:30/km') and time reps
    ('6×6 min @ 3:50/km'); returns {'count', 'distanceM', 'durationSec',
    'paceSec'} with whichever dimension the plan stated.
    """
    text = str(detail or '')
    match = _REPS_RE.search(text)
    target = None
    if match:
        distance = float(match.group(2))
        if match.group(3).lower() == 'km':
            distance *= 1000
        target = {'count': int(match.group(1)), 'distanceM': distance,
                  'durationSec': None, 'paceSec': None}
    else:
        match = _REPS_TIME_RE.search(text)
        if not match:
            return None
        unit = match.group(3).lower()
        seconds = float(match.group(2)) * (60 if unit == 'min' else 1)
        target = {'count': int(match.group(1)), 'distanceM': None,
                  'durationSec': seconds, 'paceSec': None}

    # Pace stated after the rep spec belongs to the reps themselves.
    pace = parse_pace_target(text[match.end():]) or parse_pace_target(text)
    if pace:
        target['paceSec'] = pace['lowSec']
    return target


def parse_zone(detail):
    match = _ZONE_RE.search(str(detail or ''))
    return f"Z{match.group(1)}" if match else None


def classify_session(planned, activity=None):
    """Decide which yardstick applies: easy, interval, threshold, long or race."""
    planned = planned or {}
    blob = ' '.join(str(planned.get(field) or '') for field in ('type', 'title', 'detail')).lower()
    if activity:
        blob += ' ' + str(activity.get('activityName') or '').lower()
        blob += ' ' + str((activity.get('activityType') or {}).get('typeKey') or '').lower()

    if planned.get('type') == 'race' or 'race' in blob or 'lopp' in blob:
        return 'race'
    if any(word in blob for word in INTERVAL_WORDS):
        return 'interval'
    if any(word in blob for word in THRESHOLD_WORDS):
        return 'threshold'
    if planned.get('type') == 'easy' or any(word in blob for word in EASY_WORDS):
        return 'easy'
    if any(word in blob for word in LONG_WORDS) or (planned.get('km') or 0) >= 15:
        return 'long'
    return 'run'


# ── Laps ──────────────────────────────────────────────────────────────────────

def normalize_laps(splits):
    """Flatten Garmin's split payload into comparable lap dicts."""
    laps = (splits or {}).get('lapDTOs') or (splits or {}).get('laps') or []
    out = []
    for idx, lap in enumerate(laps):
        distance = lap.get('distance') or 0
        duration = lap.get('duration') or lap.get('elapsedDuration') or 0
        speed = lap.get('averageSpeed') or lap.get('avgSpeed')
        if distance < 50:  # sub-50 m auto-laps and pauses are noise
            continue
        out.append({
            'idx': idx,
            'distanceM': float(distance),
            'durationSec': float(duration),
            'speed': float(speed) if speed else None,
            'hr': lap.get('averageHR') or lap.get('avgHR'),
            'maxHr': lap.get('maxHR'),
        })
    return out


def split_work_laps(laps):
    """Separate work reps from recovery jogs.

    Track sessions are recognised directly by their 300–550 m reps; anything
    else falls back to the largest speed gap between consecutive laps, which is
    where the rests sit.
    """
    if not laps:
        return []

    track_reps = [
        lap for lap in laps
        if 300 <= lap['distanceM'] <= 550 and lap['durationSec'] <= 150 and (lap['speed'] or 0) > 0
    ]
    if len(track_reps) >= 4:
        return sorted(track_reps, key=lambda lap: lap['idx'])

    speeds = sorted([lap['speed'] for lap in laps if lap['speed']], reverse=True)
    if not speeds:
        return laps

    # Uniform speeds mean there are no rest laps to strip — the watch simply
    # auto-lapped a continuous effort, so every lap is work.
    if speeds[-1] > 0 and speeds[0] / speeds[-1] < 1.15:
        return sorted(laps, key=lambda lap: lap['idx'])

    best_gap = None
    for i in range(len(speeds) - 1):
        if speeds[i + 1] <= 0:
            continue
        ratio = speeds[i] / speeds[i + 1]
        if ratio >= 1.15 and (best_gap is None or ratio > best_gap[0]):
            best_gap = (ratio, i)
    if best_gap is not None:
        threshold = speeds[best_gap[1] + 1] * best_gap[0] ** 0.5
        work = [lap for lap in laps if lap['speed'] and lap['speed'] >= threshold]
        if len(work) >= 2:
            return sorted(work, key=lambda lap: lap['idx'])

    threshold = speeds[max(0, len(speeds) // 2 - 1)]
    return sorted([lap for lap in laps if lap['speed'] and lap['speed'] >= threshold],
                  key=lambda lap: lap['idx'])


# ── Verdict helpers ───────────────────────────────────────────────────────────

# Below this the difference is noise, not a coaching point.
PACE_TOLERANCE_PCT = 2.0


def _pace_verdict(actual_sec, target, tolerance_pct=PACE_TOLERANCE_PCT):
    """Compare an actual pace against a target band.

    Lower seconds per km means faster, so a negative delta is "too fast".
    """
    if not actual_sec or not target:
        return None, None
    low, high = target['lowSec'], target['highSec']
    if low <= actual_sec <= high:
        return 'on_target', 0.0
    reference = low if actual_sec < low else high
    delta_pct = (actual_sec - reference) / reference * 100
    if abs(delta_pct) < tolerance_pct:
        return 'on_target', round(delta_pct, 1)
    return ('too_fast' if delta_pct < 0 else 'too_slow'), round(delta_pct, 1)


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


# ── Running ───────────────────────────────────────────────────────────────────

def analyze_run(activity, laps=None, planned=None, lactate_hr=None):
    """Judge how a run was executed against the session it was meant to be.

    `activity` is the raw Garmin activity, `laps` the output of normalize_laps,
    `planned` the plan_sessions row, `lactate_hr` the athlete's threshold heart
    rate when known (used to catch easy runs done too hard).
    """
    activity = activity or {}
    planned = planned or {}
    laps = laps or []

    distance_m = float(activity.get('distance') or 0)
    duration_sec = float(activity.get('duration') or 0)
    avg_hr = activity.get('averageHR')
    max_hr = activity.get('maxHR')

    result = {
        'kind': classify_session(planned, activity),
        'distanceKm': round(distance_m / 1000, 2) if distance_m else None,
        'durationMin': round(duration_sec / 60) if duration_sec else None,
        'avgHr': avg_hr,
        'maxHr': max_hr,
        'avgPaceSec': None,
        'avgPace': None,
        'targetPace': None,
        'paceVerdict': None,
        'paceDeltaPct': None,
        'reps': [],
        'repTarget': None,
        'repVerdict': None,
        'repDeltaPct': None,
        'fadePct': None,
        'hrDrift': None,
        'plannedKm': planned.get('km'),
        'distanceVerdict': None,
        'flags': [],
    }

    if distance_m > 0 and duration_sec > 0:
        result['avgPaceSec'] = duration_sec / (distance_m / 1000)
        result['avgPace'] = format_pace(result['avgPaceSec'])

    target_pace = parse_pace_target(planned.get('detail'))
    if target_pace:
        result['targetPace'] = target_pace

    rep_target = parse_rep_target(planned.get('detail'))
    if rep_target:
        result['repTarget'] = rep_target

    # Reps carry the real story for interval work — the session average is
    # dragged down by the recovery jogs and says nothing about execution.
    work_laps = split_work_laps(laps) if result['kind'] == 'interval' else []
    for number, lap in enumerate(work_laps, 1):
        pace_sec = speed_to_pace_seconds(lap['speed'])
        result['reps'].append({
            'n': number,
            'distanceM': round(lap['distanceM']),
            'paceSec': round(pace_sec) if pace_sec else None,
            'pace': format_pace(pace_sec),
            'hr': lap['hr'],
        })

    rep_paces = [rep['paceSec'] for rep in result['reps'] if rep['paceSec']]

    if result['kind'] == 'interval' and rep_paces:
        # The rep pace is the yardstick; when the plan only states an overall
        # target pace, that is what the reps were meant to hit.
        rep_band = None
        if rep_target and rep_target.get('paceSec'):
            rep_band = {'lowSec': rep_target['paceSec'], 'highSec': rep_target['paceSec']}
        elif target_pace:
            rep_band = target_pace
        if rep_band:
            result['repVerdict'], result['repDeltaPct'] = _pace_verdict(_mean(rep_paces), rep_band)
            if result['repVerdict'] == 'too_slow':
                result['flags'].append('reps_below_target_pace')
            elif result['repVerdict'] == 'too_fast':
                result['flags'].append('reps_above_target_pace')
        if len(rep_paces) >= 3:
            # Positive split across the reps means the session was started too hard.
            fade = (rep_paces[-1] - rep_paces[0]) / rep_paces[0] * 100
            result['fadePct'] = round(fade, 1)
            if fade > 3:
                result['flags'].append('faded_across_reps')
            elif fade < -3:
                result['flags'].append('negative_split_reps')
        if rep_target and rep_target.get('count'):
            done, planned_count = len(rep_paces), rep_target['count']
            if done < planned_count:
                result['flags'].append('fewer_reps_than_planned')
            elif done > planned_count:
                result['flags'].append('more_reps_than_planned')
    elif target_pace and result['avgPaceSec']:
        result['paceVerdict'], result['paceDeltaPct'] = _pace_verdict(result['avgPaceSec'], target_pace)
        if result['paceVerdict'] == 'too_fast' and result['kind'] in ('easy', 'long'):
            # The classic mistake: easy days run at medium effort, which steals
            # from the quality sessions later in the week.
            result['flags'].append('easy_run_too_fast')
        elif result['paceVerdict'] == 'too_fast':
            result['flags'].append('faster_than_target')
        elif result['paceVerdict'] == 'too_slow':
            # Taking an easy day genuinely easy is correct behaviour, not a
            # miss — only quality sessions owe the plan a pace.
            result['flags'].append('easy_run_slower_than_target'
                                   if result['kind'] in ('easy', 'long')
                                   else 'slower_than_target')

    # Heart rate is the second opinion — pace alone can look fine on a windy or
    # hilly day while the effort was still far too high for an easy run.
    if lactate_hr and avg_hr and result['kind'] in ('easy', 'long'):
        if avg_hr >= lactate_hr * 0.95:
            result['flags'].append('easy_run_hr_too_high')

    first_half = [lap for lap in laps[:max(1, len(laps) // 2)] if lap['hr']]
    second_half = [lap for lap in laps[max(1, len(laps) // 2):] if lap['hr']]
    if first_half and second_half:
        start_hr = _mean([lap['hr'] for lap in first_half])
        end_hr = _mean([lap['hr'] for lap in second_half])
        if start_hr:
            drift_pct = (end_hr - start_hr) / start_hr * 100
            result['hrDrift'] = {
                'firstHalf': round(start_hr),
                'secondHalf': round(end_hr),
                'pct': round(drift_pct, 1),
            }
            # Cardiac drift above ~5% on a steady run points at heat, dehydration
            # or simply starting harder than the aerobic system could hold.
            if drift_pct > 5 and result['kind'] in ('easy', 'long', 'threshold'):
                result['flags'].append('high_cardiac_drift')

    planned_km = planned.get('km')
    if planned_km and result['distanceKm']:
        delta_pct = (result['distanceKm'] - float(planned_km)) / float(planned_km) * 100
        if delta_pct <= -15:
            result['distanceVerdict'] = 'short'
            result['flags'].append('cut_session_short')
        elif delta_pct >= 15:
            result['distanceVerdict'] = 'long'
            result['flags'].append('ran_further_than_planned')
        else:
            result['distanceVerdict'] = 'on_target'

    return result


# ── Strength ──────────────────────────────────────────────────────────────────

# A lift this far under the calculated target is a real miss, not rounding to
# the nearest available plate.
STRENGTH_TOLERANCE_PCT = 7.0


def analyze_strength(logged, recommendations):
    """Compare logged lifts against the weights progression asked for.

    `logged` are rows from strength_exercises (exercise, sets, reps, weight),
    `recommendations` the output of build_strength_recommendations.
    """
    from strength_progression import canonical_exercise

    by_canonical = {}
    for item in recommendations or []:
        if item.get('canonical'):
            by_canonical[item['canonical']] = item

    exercises = []
    flags = []
    for row in logged or []:
        name = row.get('exercise') or ''
        key = canonical_exercise(name, partial=True)
        target = by_canonical.get(key) if key else None
        weight = row.get('weight')
        target_weight = (target or {}).get('weight')

        entry = {
            'exercise': name,
            'canonical': key,
            'sets': row.get('sets'),
            'reps': row.get('reps'),
            'weight': weight,
            'targetWeight': target_weight,
            'targetSets': (target or {}).get('sets'),
            'targetReps': (target or {}).get('reps'),
            'deltaPct': None,
            'verdict': None,
        }

        if weight and target_weight:
            delta_pct = (float(weight) - float(target_weight)) / float(target_weight) * 100
            entry['deltaPct'] = round(delta_pct, 1)
            if delta_pct <= -STRENGTH_TOLERANCE_PCT:
                entry['verdict'] = 'too_light'
            elif delta_pct >= STRENGTH_TOLERANCE_PCT:
                entry['verdict'] = 'heavier_than_planned'
            else:
                entry['verdict'] = 'on_target'

        target_sets = entry['targetSets']
        if target_sets and row.get('sets') and int(row['sets']) < int(target_sets):
            flags.append('missed_sets')

        exercises.append(entry)

    if any(item['verdict'] == 'too_light' for item in exercises):
        flags.append('lifted_light')
    judged = [item for item in exercises if item['verdict']]
    # "On target" only means something when nothing else went wrong.
    if judged and not flags and all(item['verdict'] == 'on_target' for item in judged):
        flags.append('strength_on_target')

    return {'exercises': exercises, 'flags': sorted(set(flags))}


# ── Prompt / UI rendering ─────────────────────────────────────────────────────

# Short Swedish verdicts shown directly in the app.
_HEADLINES = {
    'easy_run_too_fast': 'Lätt pass för snabbt',
    'easy_run_hr_too_high': 'För hög puls för lätt pass',
    'reps_below_target_pace': 'Intervallerna långsammare än mål',
    'reps_above_target_pace': 'Intervallerna snabbare än mål',
    'faded_across_reps': 'Tappade fart mot slutet',
    'negative_split_reps': 'Starkt avslut',
    'fewer_reps_than_planned': 'Färre rep än planerat',
    'more_reps_than_planned': 'Fler rep än planerat',
    'slower_than_target': 'Långsammare än måltempo',
    'easy_run_slower_than_target': 'Lugnare än måltempo',
    'faster_than_target': 'Snabbare än måltempo',
    'high_cardiac_drift': 'Hög pulsdrift',
    'cut_session_short': 'Kortare än planerat',
    'ran_further_than_planned': 'Längre än planerat',
    'lifted_light': 'Lyfte lättare än målvikt',
    'missed_sets': 'Färre set än planerat',
    'strength_on_target': 'Styrkepasset på målvikt',
}


def headline_for(analysis):
    """Pick the single most useful thing to show next to a completed session."""
    flags = (analysis or {}).get('flags') or []
    for flag in flags:
        if flag in _HEADLINES:
            return _HEADLINES[flag]
    if (analysis or {}).get('paceVerdict') == 'on_target':
        return 'Enligt plan'
    if (analysis or {}).get('repVerdict') == 'on_target':
        return 'Intervallerna på måltempo'
    return None


def describe_run(analysis, name=None):
    """Compact English lines for the LLM prompt — facts only, no judgement."""
    if not analysis:
        return ''
    parts = []
    label = name or analysis.get('kind') or 'run'
    head = [f"{label}"]
    if analysis.get('distanceKm'):
        head.append(f"{analysis['distanceKm']} km")
    if analysis.get('durationMin'):
        head.append(f"{analysis['durationMin']} min")
    if analysis.get('avgPace'):
        head.append(f"avg {analysis['avgPace']}")
    if analysis.get('avgHr'):
        head.append(f"avgHR {analysis['avgHr']}")
    parts.append(' · '.join(head))

    if analysis.get('targetPace'):
        line = f"  target pace: {analysis['targetPace']['text']}"
        if analysis.get('paceVerdict'):
            line += f" → {analysis['paceVerdict']} ({analysis['paceDeltaPct']:+.1f}%)"
        parts.append(line)

    if analysis.get('reps'):
        rep_lines = ', '.join(
            f"#{rep['n']} {rep['distanceM']}m @ {rep['pace'] or '?'}"
            + (f" HR {rep['hr']}" if rep['hr'] else '')
            for rep in analysis['reps']
        )
        parts.append(f"  work reps (rest excluded, verified from Garmin laps): {rep_lines}")
        target = analysis.get('repTarget') or {}
        if target.get('paceSec'):
            line = f"  rep target: {target.get('count')}×{int(target['distanceM'])}m @ {format_pace(target['paceSec'])}"
            if analysis.get('repVerdict'):
                line += f" → {analysis['repVerdict']} ({analysis['repDeltaPct']:+.1f}%)"
            parts.append(line)
        if analysis.get('fadePct') is not None:
            parts.append(f"  first-to-last rep drift: {analysis['fadePct']:+.1f}%")

    drift = analysis.get('hrDrift')
    if drift:
        parts.append(f"  HR drift: {drift['firstHalf']} → {drift['secondHalf']} bpm ({drift['pct']:+.1f}%)")

    if analysis.get('plannedKm') and analysis.get('distanceVerdict'):
        parts.append(f"  planned {analysis['plannedKm']} km → {analysis['distanceVerdict']}")

    if analysis.get('flags'):
        parts.append(f"  flags: {', '.join(analysis['flags'])}")

    return '\n'.join(parts)


def describe_strength(analysis):
    """Compact English lines for the LLM prompt."""
    if not analysis or not analysis.get('exercises'):
        return ''
    lines = []
    for item in analysis['exercises']:
        bits = [item['exercise']]
        if item.get('sets') and item.get('reps'):
            bits.append(f"{item['sets']}×{item['reps']}")
        if item.get('weight'):
            bits.append(f"{item['weight']} kg")
        if item.get('targetWeight'):
            bits.append(f"target {item['targetWeight']} kg")
        if item.get('verdict'):
            delta = f" ({item['deltaPct']:+.1f}%)" if item.get('deltaPct') is not None else ''
            bits.append(f"→ {item['verdict']}{delta}")
        lines.append('  ' + ' · '.join(bits))
    if analysis.get('flags'):
        lines.append(f"  flags: {', '.join(analysis['flags'])}")
    return '\n'.join(lines)
