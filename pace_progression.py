"""Derive running pace targets from measured fitness.

The running counterpart to strength_progression.py. That module already
answers "what should I lift today?" deterministically from logged history;
this one answers "what pace should this session actually be run at?".

Everything is anchored to lactate threshold pace, because that is the marker
Garmin measures directly and the one classic training zones are defined
against. From the anchor we derive a band per session kind, and any pace an
LLM proposes is validated against that band before it is allowed anywhere
near the plan.

No database or network access — callers pass in the rows they loaded.
"""

from session_analysis import format_pace

# ── Zone model ────────────────────────────────────────────────────────────────

# Seconds per km relative to lactate threshold pace. Negative is faster.
# Threshold sits at the anchor by definition; everything else is placed
# against it using the conventional physiological offsets.
ZONE_OFFSETS = {
    'interval':  (-20, -8),    # VO2max work, 400–1200 m reps
    'threshold': (0, 8),       # tempo / cruise intervals
    'race':      (8, 15),      # half-marathon effort sits just off threshold
    'long':      (45, 80),
    'easy':      (60, 95),
    'run':       (45, 80),     # unclassified steady running
}

# How far outside the band a proposal may stray before it is clamped, and
# beyond which it is rejected outright as physiologically implausible.
CLAMP_TOLERANCE_PCT = 4.0
REJECT_TOLERANCE_PCT = 12.0

# Threshold reps are run at roughly the anchor, so a well-executed threshold
# session is itself an estimate of it. Interval reps sit faster than
# threshold, so they need shifting back before they can be compared.
KIND_TO_ANCHOR_SHIFT = {'threshold': 0, 'interval': 14, 'race': -10}


def _clean_pace(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    # Anything outside 2:00–12:00 per km is a data error, not a runner.
    return value if 120 <= value <= 720 else None


def derive_anchor(lt_pace_sec=None, executions=None, max_sessions=6):
    """Estimate current lactate threshold pace.

    Garmin's own measurement updates slowly, and what the athlete has
    actually held in recent quality sessions is hard evidence. Both are
    considered and the more conservative (slower) value wins, so a single
    flattering data point cannot inflate every target in the plan.
    """
    garmin = _clean_pace(lt_pace_sec)

    measured = None
    considered = 0
    for execution in (executions or []):
        if considered >= max_sessions:
            break
        kind = execution.get('kind')
        shift = KIND_TO_ANCHOR_SHIFT.get(kind)
        if shift is None:
            continue
        reps = [rep.get('paceSec') for rep in (execution.get('reps') or []) if rep.get('paceSec')]
        if len(reps) < 2:
            continue
        considered += 1
        # The mean rep pace, not the best single rep — one fast rep says
        # nothing about the pace that can be sustained.
        estimate = _clean_pace(sum(reps) / len(reps) + shift)
        if estimate is None:
            continue
        if measured is None or estimate < measured:
            measured = estimate

    candidates = [value for value in (garmin, measured) if value]
    if not candidates:
        return {'ltPaceSec': None, 'source': 'none', 'confidence': 'none',
                'garminPaceSec': garmin, 'measuredPaceSec': measured}

    anchor = max(candidates)  # slower of the two = the conservative choice
    if garmin and measured:
        source, confidence = 'garmin+measured', 'high'
    elif garmin:
        source, confidence = 'garmin', 'medium'
    else:
        source, confidence = 'measured', 'low'

    return {
        'ltPaceSec': round(anchor),
        'ltPace': format_pace(anchor),
        'source': source,
        'confidence': confidence,
        'garminPaceSec': round(garmin) if garmin else None,
        'measuredPaceSec': round(measured) if measured else None,
    }


def target_band(kind, anchor_sec):
    """The pace band a session of this kind should be run at."""
    anchor_sec = _clean_pace(anchor_sec)
    offsets = ZONE_OFFSETS.get(kind)
    if anchor_sec is None or not offsets:
        return None
    low = round(anchor_sec + offsets[0])
    high = round(anchor_sec + offsets[1])
    return {'kind': kind, 'lowSec': low, 'highSec': high,
            'text': f"{format_pace(low)}–{format_pace(high)}"}


def validate_proposal(kind, proposed_sec, anchor_sec):
    """Check an LLM-proposed pace against what the athlete's physiology allows.

    Returns {'status', 'paceSec', 'reason'} where status is accepted, clamped
    or rejected. A clamped proposal is pulled back to the nearest edge of the
    band; a rejected one is replaced by the band itself.
    """
    band = target_band(kind, anchor_sec)
    proposed = _clean_pace(proposed_sec)
    if band is None:
        return {'status': 'rejected', 'paceSec': None,
                'reason': 'no threshold anchor available to validate against'}
    if proposed is None:
        return {'status': 'rejected', 'paceSec': band['lowSec'],
                'reason': 'proposed pace was missing or implausible'}

    if band['lowSec'] <= proposed <= band['highSec']:
        return {'status': 'accepted', 'paceSec': round(proposed),
                'reason': f"within the {kind} band {band['text']}"}

    edge = band['lowSec'] if proposed < band['lowSec'] else band['highSec']
    drift_pct = abs(proposed - edge) / edge * 100

    if drift_pct <= CLAMP_TOLERANCE_PCT:
        return {'status': 'clamped', 'paceSec': edge,
                'reason': (f"{format_pace(proposed)} sat {drift_pct:.1f}% outside the "
                           f"{kind} band {band['text']}; pulled to the nearest edge")}
    if drift_pct <= REJECT_TOLERANCE_PCT:
        return {'status': 'clamped', 'paceSec': edge,
                'reason': (f"{format_pace(proposed)} was well outside the {kind} band "
                           f"{band['text']} ({drift_pct:.1f}%); using the band edge instead")}
    return {'status': 'rejected', 'paceSec': edge,
            'reason': (f"{format_pace(proposed)} is not physiologically plausible for a "
                       f"{kind} session at threshold {format_pace(anchor_sec)}")}


def goal_feasibility(goal_pace_sec, anchor_sec, kind='race'):
    """Say plainly whether the stated goal pace is within reach.

    A half-marathon is run slightly slower than threshold, so a goal pace
    faster than the athlete's threshold is not a stretch target — it is
    currently out of reach, and the plan should say so.
    """
    goal = _clean_pace(goal_pace_sec)
    anchor = _clean_pace(anchor_sec)
    if goal is None or anchor is None:
        return None

    band = target_band(kind, anchor)
    gap_sec = round(band['lowSec'] - goal)  # positive = goal is faster than achievable

    if gap_sec <= 0:
        verdict = 'within_reach'
    elif gap_sec <= 10:
        verdict = 'stretch'
    else:
        verdict = 'out_of_reach'

    return {
        'goalPaceSec': round(goal),
        'goalPace': format_pace(goal),
        'currentCapablePaceSec': band['lowSec'],
        'currentCapablePace': format_pace(band['lowSec']),
        'gapSec': gap_sec,
        'verdict': verdict,
    }


def describe_anchor(anchor, goal=None):
    """Compact English lines for the LLM prompt."""
    if not anchor or not anchor.get('ltPaceSec'):
        return 'No threshold anchor available — do not propose specific paces.'

    lines = [
        f"Lactate threshold pace: {anchor['ltPace']} "
        f"(source: {anchor['source']}, confidence: {anchor['confidence']})"
    ]
    if anchor.get('garminPaceSec') and anchor.get('measuredPaceSec'):
        lines.append(
            f"  Garmin measured {format_pace(anchor['garminPaceSec'])}; "
            f"recent quality sessions imply {format_pace(anchor['measuredPaceSec'])}"
        )
    lines.append('Allowed pace bands derived from that threshold:')
    for kind in ('interval', 'threshold', 'race', 'long', 'easy'):
        band = target_band(kind, anchor['ltPaceSec'])
        if band:
            lines.append(f"  {kind}: {band['text']}")
    if goal:
        lines.append(
            f"Goal pace {goal['goalPace']} vs currently achievable "
            f"{goal['currentCapablePace']} → {goal['verdict']}"
            + (f" (short by {goal['gapSec']} s/km)" if goal['gapSec'] > 0 else '')
        )
    lines.append(
        'Propose a target pace for each session inside the matching band. '
        'Any pace outside its band will be clamped or rejected.'
    )
    return '\n'.join(lines)
