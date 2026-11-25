"""A strength programme for a runner that follows the running season.

Two sessions a week (A and B) while the running is least specific, then fewer
sets, then one maintenance session, then nothing in race week — the load key
comes from season_plan.STRENGTH_BY_PHASE. Every exercise is in the
strength_progression catalogue, so the weight for each set is proposed from the
athlete's own log (double progression, RIR-aware) instead of a fixed number.

A is the squat day, B the hinge day. Both carry single-leg work and heavy calf
raises: for a runner the calf/soleus and the hip stabilisers carry more load
per stride than any big lift, and they are what keeps the feet and shins quiet
as the running volume climbs. Upper body stays in for posture, arm drive and
the athlete's own goal of looking good.

Niggles (see niggles.py) take exercises out: a foot or calf problem removes
jumps and heavy calf raises, a knee problem the squat and split squat, a back
problem the deadlift.
"""

from strength_progression import EXERCISES, _format_weight, build_strength_recommendations

# (övning, set, reps) i katalogens namn; set skalas med fasen.
SESSIONS = {
    'A': {'title': 'Styrka A · knäböj + press',
          'focus': 'Knäböj, enbensstyrka, vader och press.',
          'items': [('Knäböj', 4, '5'), ('Bulgariska utfall', 3, '8'), ('Vadpress', 3, '10-12'),
                    ('Bänkpress', 3, '6'), ('Rodd', 3, '8'), ('Dead bug', 3, '10')]},
    'B': {'title': 'Styrka B · marklyft + drag',
          'focus': 'Bakre kedja, höft, vader och drag.',
          'items': [('Marklyft', 3, '5'), ('RDL', 2, '8'), ('Vadpress', 3, '10-12'),
                    ('Axelpress', 3, '6'), ('Latsdrag', 3, '8'), ('Chins', 3, '6'),
                    ('Rygghäv', 2, '12')]},
    'M': {'title': 'Styrka · underhåll',
          'focus': 'Håll styrkan utan att trötta benen: tungt men få set, explosivt.',
          'items': [('Knäböj', 2, '4'), ('Boxjumps', 3, '5'), ('Vadpress', 2, '10'),
                    ('Bänkpress', 2, '6'), ('Rodd', 2, '8'), ('Dead bug', 2, '10')]},
}

# Vilka pass och hur seten skalas per belastningsnyckel från säsongsplanen.
LOAD = {
    'heavy': {'sessions': ['A', 'B'], 'sets': 1.0,
              'note': 'Tungt med 1–2 reps kvar. Lägg passen på lugna dagar eller samma dag som ett hårt löppass, aldrig dagen före långpasset eller ett kvalitetspass.'},
    'heavy_reduced': {'sessions': ['A', 'B'], 'sets': 0.75,
                      'note': 'Samma vikter men färre set, så att benen är pigga till loppfartspassen. Aldrig till failure.'},
    'maintain': {'sessions': ['M'], 'sets': 1.0,
                 'note': 'Ett pass i veckan räcker för att behålla styrkan. Explosivt och tungt, men få set.'},
    'maintain_light': {'sessions': ['M'], 'sets': 0.5,
                       'note': 'Ett kort pass tidigt i veckan, halva seten. Inget de sista fem dagarna före loppet.'},
    'light': {'sessions': ['A'], 'sets': 0.67,
              'note': 'Avlastningsvecka: samma övningar, färre set och cirka 10 % lättare.'},
    'none': {'sessions': [], 'sets': 0, 'note': 'Ingen styrka loppveckan – benen ska vara utvilade.'},
}

# Övningar som stryks vid en aktiv känning i ett visst område (niggles.AREAS).
AVOID_BY_AREA = {
    'foot': {'Boxjumps', 'Vadpress'},
    'achilles': {'Boxjumps', 'Vadpress'},
    'calf': {'Boxjumps', 'Vadpress'},
    'shin': {'Boxjumps'},
    'knee': {'Knäböj', 'Bulgariska utfall', 'Boxjumps'},
    'hip': {'Bulgariska utfall'},
    'hamstring': {'RDL', 'Marklyft'},
    'back': {'Marklyft', 'RDL', 'Knäböj'},
    'shoulder': {'Axelpress', 'Bänkpress', 'Chins'},
}

# Det som ersätter en struken övning, så att passet inte bara blir kortare.
SUBSTITUTE = {
    'Knäböj': 'Benpress',
    'Marklyft': 'Benpress',
}


def _scaled_sets(sets, factor):
    return max(1, int(round(sets * factor)))


def program(load_key, history, today, active_areas=()):
    """The week's strength sessions with a proposed weight per exercise.

    `history` is the strength log in strength_progression's shape; `active_areas`
    are body areas with an active niggle."""
    spec = LOAD.get(load_key) or LOAD['heavy']
    avoid = set()
    for area in active_areas or ():
        avoid |= AVOID_BY_AREA.get(area, set())
    sessions = []
    for key in spec['sessions']:
        base = SESSIONS[key]
        lines, removed = [], []
        for name, sets, reps in base['items']:
            if name in avoid:
                removed.append(name)
                substitute = SUBSTITUTE.get(name)
                if substitute and substitute in {e['label'] for e in EXERCISES.values()} and substitute not in avoid:
                    lines.append(f'{substitute} {_scaled_sets(sets, spec["sets"])}x{reps}')
                continue
            lines.append(f'{name} {_scaled_sets(sets, spec["sets"])}x{reps}')
        detail = '; '.join(lines)
        recs = build_strength_recommendations(detail, history, before_date=today.isoformat())
        if load_key == 'light':
            for rec in recs:
                if rec.get('weight'):
                    rec['weight'] = round(rec['weight'] * 0.9 / 2.5) * 2.5
                    rec['prescription'] = rec['prescription'].rsplit('@', 1)[0].strip() + f" @ {_format_weight(rec['weight'])} kg"
                    rec['reason'] = 'Avlastningsvecka: cirka 10 % lättare än nästa vanliga steg.'
        sessions.append({
            'key': key, 'title': base['title'], 'focus': base['focus'], 'detail': detail,
            'exercises': recs,
            'removed': removed,
        })
    return {'load': load_key, 'note': spec['note'], 'sessions': sessions, 'avoided': sorted(avoid)}


def describe(prog):
    """English lines for the coach prompts."""
    if not prog or not prog.get('sessions'):
        return f"STRENGTH THIS WEEK: none ({(prog or {}).get('note', '')})"
    lines = [f"STRENGTH PROGRAMME THIS WEEK (Trainyze, follows the running phase): {prog['note']}"]
    for s in prog['sessions']:
        lines.append(f"  {s['title']}: " + '; '.join(e.get('prescription') or e.get('exercise', '') for e in s['exercises']))
        if s['removed']:
            lines.append(f"    removed because of an active niggle: {', '.join(s['removed'])}")
    return '\n'.join(lines)
