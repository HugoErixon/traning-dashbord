"""The Sunday report: how the week went against its season frame, and what next.

Computed, not AI-written — like the morning report it runs in the background
and must not depend on LLM quota, and a push notification has room for a
verdict, not an essay. The full report is shown on the Plan page.
"""

RUN_TYPES = ('running', 'track_running', 'treadmill_running', 'trail_running')
STRENGTH_TYPES = ('strength_training', 'indoor_cardio')
QUIET_TYPES = ('walking', 'hiking')

VERDICT_TEXT = {
    'on_target': 'på målet', 'too_fast': 'snabbare än målet', 'too_slow': 'långsammare än målet',
    'too_short': 'kortare än planerat', 'too_long': 'längre än planerat',
}


def _km(meters):
    return (meters or 0) / 1000


def _dec(value):
    return f'{value:.1f}'.replace('.', ',').replace(',0', '')


def _execution_text(session):
    ex = session.get('execution') or {}
    verdict = ex.get('repVerdict') or ex.get('paceVerdict')
    if not verdict:
        return None
    text = VERDICT_TEXT.get(verdict, verdict)
    delta = ex.get('repDeltaPct') if ex.get('repVerdict') else ex.get('paceDeltaPct')
    if delta not in (None, 0) and verdict != 'on_target':
        text += f' ({delta:+.1f} %)'.replace('.', ',')
    return text


def build(frame, next_frame, activities, sessions, niggle_rows, strength_logged_days):
    """`frame`/`next_frame` are season_plan weeks (or None), `activities` rows
    with type/distance/raw for the week, `sessions` the week's plan rows."""
    runs = [a for a in activities if (a.get('type') or '') in RUN_TYPES]
    run_km = sum(_km(a.get('distance')) for a in runs)
    longest = max((_km(a.get('distance')) for a in runs), default=0)
    load = sum(((a.get('raw') or {}).get('activityTrainingLoad') or 0) for a in activities)
    strength_days = set(strength_logged_days or ())
    for a in activities:
        if (a.get('type') or '') in STRENGTH_TYPES:
            strength_days.add(str(a.get('date') or '')[:10])
    other = [a for a in activities
             if (a.get('type') or '') not in RUN_TYPES + STRENGTH_TYPES + QUIET_TYPES]
    other_min = round(sum(((a.get('raw') or {}).get('duration') or 0) for a in other) / 60)

    quality = [s for s in sessions if s.get('type') in ('run', 'race')]
    done_quality = [s for s in quality if s.get('status') == 'completed']
    missed = [s for s in sessions if s.get('status') in ('missed', 'skipped') and s.get('type') != 'rest']

    lines = []
    target = frame['targetKm'] if frame else None
    if target:
        ratio = run_km / target if target else 0
        verdict = 'on_target' if 0.9 <= ratio <= 1.12 else 'under' if ratio < 0.9 else 'over'
        lines.append(f"Löpning {run_km:.0f} av {target} km ({round(ratio * 100)} %), "
                     f"{len(runs)} pass, längsta {_dec(longest)} km (mål {frame['longRunKm']} km).")
    else:
        verdict = None
        lines.append(f'Löpning {run_km:.0f} km på {len(runs)} pass, längsta {_dec(longest)} km.')

    if quality:
        parts = []
        for s in quality:
            if s.get('status') == 'completed':
                how = _execution_text(s)
                parts.append(f"{s['title']}: {how}" if how else f"{s['title']}: genomfört")
            elif s.get('status') in ('missed', 'skipped'):
                parts.append(f"{s['title']}: missat")
        if parts:
            lines.append('Kvalitetspass: ' + '; '.join(parts) + '.')
    if missed:
        lines.append(f"Missade pass: {', '.join(s['title'] for s in missed)}.")

    strength_target = (frame or {}).get('strength', {}).get('sessions')
    if strength_target is not None:
        lines.append(f'Styrka: {len(strength_days)} av {strength_target} pass.')
    elif strength_days:
        lines.append(f'Styrka: {len(strength_days)} pass.')
    if other:
        names = sorted({(a.get('type') or 'annat').replace('_', ' ') for a in other})
        lines.append(f"Övrig belastning: {len(other)} pass ({', '.join(names)}), {other_min} min. "
                     'Matcher och domaruppdrag räknas in i veckans totala belastning.')
    if load:
        lines.append(f'Garmins belastning för veckan: {round(load)}.')

    active = [r for r in niggle_rows or [] if r.get('active')]
    for r in active:
        trend = {'worse': ', värre', 'better': ', bättre', 'same': ''}.get(r.get('trend'), '')
        lines.append(f"Känning: {r['label']} {r['severity']}/10{trend}.")

    if verdict == 'on_target' and len(done_quality) == len(quality):
        headline = 'Veckan i fas med planen'
    elif verdict == 'under':
        headline = 'Veckan blev kortare än planen'
    elif verdict == 'over':
        headline = 'Mer volym än planerat'
    elif verdict == 'on_target':
        headline = 'Volymen i fas, men kvalitet saknades'
    else:
        headline = 'Veckan som gick'

    advice = []
    worse = [r for r in active if r.get('trend') == 'worse' or r['severity'] >= 4]
    if worse:
        advice.append(f"{worse[0]['label']} styr nästa vecka: håll löpningen lugn tills känningen är under 3/10.")
    elif verdict == 'under' and target and run_km < target * 0.75:
        advice.append('Bygg inte ikapp: kör nästa veckas ram som den är, inte mer.')
    elif verdict == 'over':
        advice.append('Håll nästa vecka inom ramen så att ökningen hinner sätta sig.')

    nxt = None
    if next_frame:
        nxt = {
            'isoWeek': next_frame['isoWeek'], 'phase': next_frame['phaseLabel'],
            'targetKm': next_frame['targetKm'], 'longRunKm': next_frame['longRunKm'],
            'recovery': next_frame['recovery'],
            'keySessions': [s['title'] for s in next_frame['keySessions']],
        }
        tag = ' (avlastning)' if next_frame['recovery'] else ''
        advice.append(f"Nästa vecka{tag}: {next_frame['phaseLabel'].lower()}, {next_frame['targetKm']} km, "
                      f"långpass {next_frame['longRunKm']} km. Nyckelpass: "
                      + ', '.join(s['title'] for s in next_frame['keySessions']) + '.')

    push_body = lines[0]
    if advice:
        push_body += ' ' + advice[0]
    return {
        'headline': headline, 'verdict': verdict, 'lines': lines, 'advice': advice, 'next': nxt,
        'runKm': round(run_km, 1), 'targetKm': target, 'longestKm': round(longest, 1),
        'qualityDone': len(done_quality), 'qualityPlanned': len(quality),
        'strengthDone': len(strength_days), 'strengthTarget': strength_target,
        'push': {'title': f'Veckorapport: {headline.lower()}', 'body': push_body[:230]},
    }
