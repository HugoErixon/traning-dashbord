import unittest
from datetime import date
from itertools import combinations, product

import plan_calendar
import season_plan
import training_engine as engine
import vdot


DISTANCES = ('5k', '10k', 'half', 'marathon')
LEVELS = ('', 'new', 'recreational', 'experienced', 'competitive')
PHASES = ('base', 'specific', 'sharpen', 'taper', 'race')
QUALITY_KINDS = ('subt_short', 'subt_long', 'threshold', 'vo2', 'speed', 'hills', 'race_pace')


def frame(phase='base', target=70, index=0, recovery=False, long_km=None):
    return {'week': 48, 'phase': phase, 'recovery': recovery, 'targetKm': target,
            'longRunKm': target * 0.3 if long_km is None else long_km,
            'indexInPhase': index,
            'strength': season_plan.STRENGTH_BY_PHASE['recovery' if recovery else phase]}


def profile(level='competitive', days=7, **kwargs):
    return {'experience': level, 'preferredDays': list(engine.DEFAULT_DAYS[days]),
            'sessionsPerWeek': days, **kwargs}


def runs(week):
    return [s for s in week['sessions'] if s['type'] in ('run', 'easy', 'race')]


def quality(week):
    return [s for s in week['sessions'] if s['kind'] in QUALITY_KINDS]


def check(week, rule):
    return next(c for c in week['checks'] if c['rule'] == rule)


class WeekTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.zones = vdot.paces(55.2)

    def test_all_distances_levels_days_phases_and_recovery(self):
        # 3 000 veckor, inklusive flera steg i passbankens progression.
        cases = product(DISTANCES, LEVELS, range(3, 8), PHASES, (False, True), (0, 3, 5))
        for distance, level, days, phase, recovery, index in cases:
            with self.subTest(distance=distance, level=level, days=days, phase=phase,
                              recovery=recovery, index=index):
                target = {'new': 25, 'recreational': 35, '': 45,
                          'experienced': 55, 'competitive': 70}[level]
                if recovery or phase in ('taper', 'race'):
                    target *= 0.7
                week = engine.build_week(frame(phase, target, index, recovery), self.zones,
                                         profile(level, days), distance, lthr=180)
                self.assertTrue(runs(week))
                for session in week['sessions']:
                    self.assertIn(session['dow'], range(7))
                    for field in ('title', 'detail', 'purpose'):
                        self.assertTrue(session[field].strip())
                    self.assertIn('slot', session)
                    self.assertIsNone(session['slot'])
                    self.assertGreaterEqual(session['minutes'], 0)
                    if session['type'] != 'lift':
                        self.assertGreater(session['km'], 0)
                        self.assertGreater(session['minutes'], 0)
                        self.assertLessEqual(session['qualityMin'], session['minutes'])
                summary = week['summary']
                self.assertGreaterEqual(summary['easyTimeShare'], 0)
                self.assertLessEqual(summary['easyTimeShare'], 1)
                self.assertIn('easyShare', summary)
                self.assertLessEqual(summary['hardDays'], 3)
                self.assertTrue(check(week, 'Högst tre hårda dagar')['ok'])
                if days >= 5:
                    self.assertTrue(check(week, 'Vila mellan hårda pass')['ok'])
                    hard_days = sorted({s['dow'] for s in quality(week)})
                    self.assertTrue(all(b - a >= 2 for a, b in zip(hard_days, hard_days[1:])))
                if phase == 'race':
                    self.assertEqual(len(quality(week)), 1)
                    race = [s for s in runs(week) if s['kind'] == 'race']
                    self.assertEqual(len(race), 1)
                    self.assertEqual(race[0]['dow'], 6)
                else:
                    long = next(s for s in runs(week) if s['kind'] in ('long', 'long_mp'))
                    total = sum(s['km'] for s in runs(week))
                    self.assertLessEqual(long['km'], total * 0.30 + 1e-9)
                    self.assertLessEqual(long['km'] * self.zones['E']['paceSec'] / 60, 150)
                if phase == 'taper':
                    self.assertTrue(any(s['qualityMin'] > 0 for s in quality(week)))

    def test_season_frames_can_be_used_directly(self):
        for distance in DISTANCES:
            with self.subTest(distance=distance):
                frames = season_plan.build(date(2027, 3, 14), distance, 60, 16,
                                           plan_calendar.plan_week(date(2026, 10, 5)), self.zones)
                self.assertTrue(any(f['recovery'] for f in frames))
                for f in frames:
                    week = engine.build_week(f, self.zones, profile(), distance)
                    self.assertTrue(week['sessions'])
                    self.assertEqual(week['summary']['phase'], f['phase'])

    def test_long_run_uses_requested_available_day(self):
        for count in range(3, 8):
            for days in combinations(range(7), count):
                for long_day in days:
                    with self.subTest(days=days, long_day=long_day):
                        p = profile(preferredDays=list(days), longRunDay=long_day)
                        week = engine.build_week(frame(), self.zones, p)
                        long = next(s for s in runs(week) if s['kind'] in ('long', 'long_mp'))
                        self.assertEqual(long['dow'], long_day)
                        if count >= 5:
                            self.assertTrue(check(week, 'Vila mellan hårda pass')['ok'])

    def test_race_day_is_added_even_outside_preferred_days(self):
        for distance, race_day in product(DISTANCES, range(7)):
            with self.subTest(distance=distance, race_day=race_day):
                p = profile(preferredDays=[d for d in range(7) if d != race_day])
                week = engine.build_week(frame('race'), self.zones, p, distance,
                                         race_dow=race_day, race_label='Testlopp')
                race = next(s for s in runs(week) if s['kind'] == 'race')
                self.assertEqual(race['dow'], race_day)
                self.assertEqual(race['title'], 'Testlopp')
                self.assertEqual(len(quality(week)), 1)

    def test_competitive_keeps_seven_days_at_60_to_80_km(self):
        for target, index in product((60, 70, 80), range(6)):
            with self.subTest(target=target, index=index):
                week = engine.build_week(frame(target=target, index=index), self.zones, profile())
                self.assertEqual({s['dow'] for s in runs(week)}, set(range(7)))
                self.assertEqual(len(quality(week)), 3)
                for session in runs(week):
                    if session['type'] == 'easy':
                        self.assertGreaterEqual(session['km'], 3)
                self.assertAlmostEqual(week['summary']['km'], target, delta=target * 0.12)
                self.assertTrue(check(week, 'Veckans volym')['ok'])

    def test_short_easy_runs_are_allowed_only_for_higher_levels(self):
        for level in LEVELS:
            with self.subTest(level=level):
                week = engine.build_week(frame(target=35, index=1), self.zones, profile(level))
                easy = [s for s in runs(week) if s['type'] == 'easy']
                self.assertTrue(easy)
                minimum = 3 if level in ('competitive', 'experienced') else 4
                self.assertTrue(all(s['km'] >= minimum for s in easy))

    def test_padding_is_shortened_before_an_easy_day_is_removed(self):
        week = engine.build_week(frame(target=50, index=1), self.zones, profile())
        self.assertEqual(week['summary']['runDays'], 7)
        self.assertTrue(any(3 <= s['km'] < 4 for s in runs(week) if s['type'] == 'easy'))
        for session in quality(week):
            warm, work, cool = session['structure']
            self.assertEqual(warm['km'], 2)
            self.assertEqual(cool['km'], 1.5)
            self.assertIn('2 km uppvärmning', warm['text'])
            self.assertIn('1.5 km nedjogg', cool['text'])
            self.assertEqual(session['detail'], ' · '.join(p['text'] for p in session['structure']))
            self.assertAlmostEqual(session['km'], 3.5 + work['km'] + work['restKm'], delta=0.051)
            self.assertAlmostEqual(session['minutes'], 3.5 * self.zones['E']['paceSec'] / 60
                                   + work['minutes'] + work['restMinutes'], delta=0.01)
        self.assertAlmostEqual(week['summary']['km'], 50, delta=0.2)

    def test_easy_day_removed_only_after_shortening_when_still_over_budget(self):
        week = engine.build_week(frame(target=40), self.zones, profile())
        self.assertLess(week['summary']['runDays'], 7)
        for session in quality(week):
            self.assertEqual(session['structure'][0]['km'], 2)
            self.assertEqual(session['structure'][2]['km'], 1.5)
        self.assertTrue(check(week, 'Veckans volym')['ok'])

    def test_long_run_caps_are_not_rounded_up(self):
        for target in (31, 57, 101, 160):
            with self.subTest(target=target):
                week = engine.build_week(frame(target=target, long_km=60), self.zones, profile())
                long = next(s for s in runs(week) if s['kind'] == 'long')
                self.assertLessEqual(long['km'], 0.3 * sum(s['km'] for s in runs(week)) + 1e-6)
                self.assertLessEqual(long['km'] * self.zones['E']['paceSec'] / 60, 150)

    def test_from_dow_filters_running_and_strength_including_double_threshold(self):
        for phase, start in product(PHASES, range(8)):
            with self.subTest(phase=phase, start=start):
                full = engine.build_week(frame(phase), self.zones, profile(sessionsPerWeek=9))
                partial = engine.build_week(frame(phase), self.zones,
                                            profile(sessionsPerWeek=9), from_dow=start)
                self.assertEqual(partial['sessions'], [s for s in full['sessions'] if s['dow'] >= start])
                self.assertAlmostEqual(partial['summary']['km'], sum(s['km'] for s in runs(partial)), places=6)
                self.assertGreaterEqual(partial['summary']['easyTimeShare'], 0)
                self.assertLessEqual(partial['summary']['easyTimeShare'], 1)


class QualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.zones = vdot.paces(55.2)

    def test_daniels_caps_across_library_progression(self):
        for distance, target, kind in product(DISTANCES, (20, 35, 50, 70, 100, 200), QUALITY_KINDS):
            for index in range(len(engine.LIBRARY[kind])):
                with self.subTest(distance=distance, target=target, kind=kind, index=index):
                    session = engine.quality_session(kind, index, target, self.zones, distance)
                    work = session['structure'][1]
                    if work['reps'] <= 2:
                        continue
                    cap = {'I': min(0.08 * target, 10), 'R': min(0.05 * target, 8),
                           'T': 0.10 * target, 'SUBT': 0.12 * target, 'RACE': 0.12 * target}[work['zone']]
                    # Använd oavrundad arbetsdistans: tiondelar får inte dölja ett överskridet tak.
                    self.assertLessEqual(work['km'], cap + 1e-9)

    def test_double_threshold_slots_and_unique_hard_days(self):
        for distance, phase in product(DISTANCES, ('base', 'specific')):
            with self.subTest(distance=distance, phase=phase):
                week = engine.build_week(frame(phase, target=90), self.zones,
                                         profile(sessionsPerWeek=9), distance)
                doubles = [s for s in runs(week) if s['slot'] is not None]
                self.assertEqual([s['slot'] for s in doubles], ['am', 'pm'])
                self.assertEqual(doubles[0]['dow'], doubles[1]['dow'])
                self.assertTrue(all(s['kind'].startswith('subt') for s in doubles))
                self.assertEqual(len(quality(week)), 4)
                summary, checks = engine.check_week(week['sessions'], 90, phase)
                self.assertEqual(summary['hardDays'], 3)
                self.assertTrue(next(c for c in checks if c['rule'] == 'Högst tre hårda dagar')['ok'])
                self.assertTrue(check(week, 'Vila mellan hårda pass')['ok'])

    def test_double_threshold_requires_all_conditions(self):
        for level, sessions, phase, recovery in product(LEVELS, (7, 8, 9), PHASES, (False, True)):
            with self.subTest(level=level, sessions=sessions, phase=phase, recovery=recovery):
                week = engine.build_week(frame(phase, recovery=recovery), self.zones,
                                         profile(level, sessionsPerWeek=sessions))
                expected = level == 'competitive' and sessions >= 9 and phase in ('base', 'specific') and not recovery
                self.assertEqual(sum(s['slot'] is not None for s in week['sessions']), 2 if expected else 0)


class TimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.zones = vdot.paces(55.2)

    def test_easy_and_quality_minutes_include_recovery_once(self):
        easy = engine.easy_run(10, self.zones)
        self.assertAlmostEqual(easy['minutes'], 10 * self.zones['E']['paceSec'] / 60, delta=0.01)
        # Första VO2-passet: 5 × 1 km, fyra vilor, 4,5 km upp-/nedjogg.
        interval = engine.quality_session('vo2', 0, 100, self.zones, '5k')
        hard_time = 5 * self.zones['I']['paceSec'] / 60
        expected = 4.5 * self.zones['E']['paceSec'] / 60 + hard_time + 4
        self.assertAlmostEqual(interval['qualityMin'], hard_time, delta=0.01)
        self.assertAlmostEqual(interval['minutes'], expected, delta=0.01)
        # Sammanhängande tröskel har ingen vila.
        threshold = engine.quality_session('threshold', 3, 100, self.zones, 'half')
        self.assertAlmostEqual(threshold['minutes'], 4.5 * self.zones['E']['paceSec'] / 60 + 20, delta=0.01)

    def test_long_run_and_race_include_time_at_actual_zone_pace(self):
        goal = vdot.paces(60)
        long = engine.long_run(20, self.zones, 'specific', 1, 'half', goal_zones=goal)
        hard_time = 5 * goal['HM']['paceSec'] / 60
        self.assertEqual(long['qualityKm'], 5)
        self.assertAlmostEqual(long['qualityMin'], hard_time, delta=0.01)
        self.assertAlmostEqual(long['minutes'], 15 * self.zones['E']['paceSec'] / 60 + hard_time, delta=0.01)
        progressive = engine.long_run(20, self.zones, 'base', 1, 'half')
        self.assertEqual(progressive['qualityKm'], 3)
        self.assertGreater(progressive['qualityMin'], 0)
        self.assertLess(progressive['minutes'], 20 * self.zones['E']['paceSec'] / 60)
        for distance, km, zone in (('5k', 5, 'I'), ('10k', 10, 'T'), ('half', 21.1, 'HM'), ('marathon', 42.2, 'M')):
            with self.subTest(distance=distance):
                week = engine.build_week(frame('race'), self.zones, profile(), distance, goal_zones=goal)
                race = next(s for s in runs(week) if s['kind'] == 'race')
                expected = km * goal[zone]['paceSec'] / 60
                self.assertAlmostEqual(race['qualityMin'], expected, delta=0.01)
                self.assertAlmostEqual(race['minutes'], expected + 2 * self.zones['E']['paceSec'] / 60, delta=0.01)

    def test_80_20_uses_time_not_distance_or_strength(self):
        # Samma 80 % lugna km kan ha för mycket kvalitetstid.
        sessions = [{'kind': 'easy', 'type': 'easy', 'dow': 0, 'km': 8, 'minutes': 40,
                     'qualityKm': 0, 'qualityMin': 0},
                    {'kind': 'threshold', 'type': 'run', 'dow': 2, 'km': 2, 'minutes': 20,
                     'qualityKm': 2, 'qualityMin': 20},
                    {'kind': 'strength', 'type': 'lift', 'dow': 2, 'km': 0, 'minutes': 100}]
        summary, checks = engine.check_week(sessions, 10, 'base')
        self.assertEqual(summary['easyShare'], 0.8)
        self.assertEqual(summary['easyTimeShare'], 0.67)
        self.assertFalse(checks[0]['ok'])
        self.assertIn('80 % av veckans km', checks[0]['text'])
        self.assertIn('67 % av tiden', checks[0]['text'])
        # Exakt 75 % är godkänt; strax under är det inte, oavsett avrundningen.
        sessions[0]['minutes'] = 60
        self.assertTrue(engine.check_week(sessions, 10, 'base')[1][0]['ok'])
        sessions[0]['minutes'] = 59.9
        self.assertFalse(engine.check_week(sessions, 10, 'base')[1][0]['ok'])
        for phase in ('taper', 'race'):
            self.assertTrue(engine.check_week(sessions, 10, phase)[1][0]['ok'])
        summary, _ = engine.check_week([], 0, 'base')
        self.assertEqual(summary['easyTimeShare'], 1)

    def test_summary_time_share_matches_all_running_minutes(self):
        week = engine.build_week(frame('specific', target=90), self.zones, profile(sessionsPerWeek=9))
        total = sum(s['minutes'] for s in runs(week))
        hard = sum(s['qualityMin'] for s in runs(week))
        self.assertAlmostEqual(week['summary']['easyTimeShare'], 1 - hard / total, delta=0.005)


if __name__ == '__main__':
    unittest.main()
