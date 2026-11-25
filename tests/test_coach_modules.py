"""Säsongsplan, labbtest, känningar, styrkeprogram och veckorapport."""
import unittest
from datetime import date

import lab_tests
import niggles
import season_plan
import strength_program
import vdot
import weekly_report

RACE = date(2027, 3, 7)


class SeasonPlanTests(unittest.TestCase):
    def build(self, **kw):
        return season_plan.build(RACE, 'half', 54, 18, 48, zones=vdot.paces(56), **kw)

    def test_phases_count_back_from_the_race_week(self):
        weeks = self.build()
        self.assertEqual(weeks[-1]['phase'], 'race')
        self.assertEqual(weeks[-1]['isoWeek'], 9)
        self.assertEqual(weeks[-2]['phase'], 'taper')
        self.assertEqual([w['phase'] for w in weeks[-4:-2]], ['sharpen', 'sharpen'])
        self.assertEqual(weeks[0]['phase'], 'base')
        self.assertEqual(len(weeks), 62 - 48 + 1)  # över nyår

    def test_recovery_weeks_and_christmas(self):
        weeks = {w['isoWeek']: w for w in self.build()}
        self.assertTrue(weeks[50]['recovery'])
        self.assertTrue(weeks[5]['recovery'])
        self.assertFalse(weeks[1]['recovery'])  # direkt efter julen
        self.assertTrue(weeks[52]['holiday'])
        self.assertLess(weeks[50]['targetKm'], weeks[49]['targetKm'])

    def test_volume_ramp_is_bounded(self):
        weeks = self.build()
        peak = max(w['targetKm'] for w in weeks)
        self.assertLessEqual(peak, round(54 * 1.2) + 1)
        self.assertLess(weeks[-1]['targetKm'], peak * 0.6)

    def test_peak_override(self):
        weeks = self.build(peak_override=70)
        self.assertEqual(max(w['targetKm'] for w in weeks), 70)

    def test_key_sessions_carry_vdot_paces(self):
        week = self.build()[0]
        threshold = week['keySessions'][0]
        self.assertIn(vdot.paces(56)['T']['pace'].replace('/km', ''), threshold['detail'])

    def test_describe_mentions_target(self):
        text = season_plan.describe(self.build(), 48)
        self.assertIn('target', text)
        self.assertIn('calendar week 48', text)


class LabTestTests(unittest.TestCase):
    def test_speed_or_pace_and_validation(self):
        t = lab_tests.normalize({'date': '2026-11-29', 'lt1Hr': 158, 'lt1Speed': '13,5',
                                 'lt2Hr': 180, 'lt2Pace': '3:58'}, today=date(2026, 11, 30))
        self.assertEqual(t['lt1PaceSec'], 267)
        self.assertEqual(t['lt2PaceSec'], 238)
        with self.assertRaises(ValueError):
            lab_tests.normalize({'date': '2026-11-29', 'lt1Hr': 185, 'lt2Hr': 180}, today=date(2026, 11, 30))
        with self.assertRaises(ValueError):
            lab_tests.normalize({'date': '2026-12-29', 'lt2Hr': 180}, today=date(2026, 11, 30))

    def test_lab_pace_is_a_vdot_source_above_garmin(self):
        info = vdot.current(lt_pace_sec=242, lab={'paceSec': 238, 'date': '2026-11-29'})
        self.assertEqual(info['source'], 'lab')
        self.assertEqual(info['alternatives'][0]['source'], 'threshold')

    def test_zones(self):
        zones = lab_tests.hr_zones({'lt1Hr': 158, 'lt2Hr': 180, 'maxHr': 196})
        self.assertEqual([z['key'] for z in zones], ['Z1', 'Z2', 'Z3', 'Z4', 'Z5'])
        self.assertEqual(zones[1]['high'], 158)


class NiggleTests(unittest.TestCase):
    def test_latest_reading_decides_and_zero_closes(self):
        today = date(2026, 11, 25)
        entries = []
        for day, sev in (('2026-11-20', 3), ('2026-11-21', 5)):
            entries = niggles.add(entries, dict(niggles.normalize(
                {'area': 'foot', 'side': 'left', 'severity': sev}, date.fromisoformat(day))), 1)
        rows = niggles.status(entries, today)
        self.assertTrue(rows[0]['active'])
        self.assertEqual(rows[0]['trend'], 'worse')
        self.assertTrue(rows[0]['needsCheckIn'])
        self.assertEqual(niggles.active_areas(rows), ['foot'])
        entries = niggles.add(entries, niggles.normalize(
            {'area': 'foot', 'side': 'left', 'severity': 0}, today), 2)
        self.assertFalse(niggles.status(entries, today)[0]['active'])

    def test_niggle_removes_exercises_from_the_program(self):
        prog = strength_program.program('heavy', [], date(2026, 11, 25), active_areas=['foot'])
        names = [e['exercise'] for s in prog['sessions'] for e in s['exercises']]
        self.assertNotIn('Vadpress', names)
        self.assertIn('Vadpress', prog['sessions'][0]['removed'])


class StrengthProgramTests(unittest.TestCase):
    def test_phase_decides_sessions_and_weights_come_from_the_log(self):
        history = [{'id': i, 'sessionId': f'2026-11-{10 + i}', 'exercise': 'Marklyft', 'sets': 3,
                    'reps': '5', 'weight': 90.0, 'note': '', 'date': f'2026-11-{10 + i}'} for i in range(2)]
        heavy = strength_program.program('heavy', history, date(2026, 11, 25))
        self.assertEqual([s['key'] for s in heavy['sessions']], ['A', 'B'])
        deadlift = next(e for e in heavy['sessions'][1]['exercises'] if e['exercise'] == 'Marklyft')
        self.assertEqual(deadlift['weight'], 92.5)
        self.assertEqual([s['key'] for s in strength_program.program('maintain', history, date(2026, 11, 25))['sessions']], ['M'])
        self.assertEqual(strength_program.program('none', history, date(2026, 11, 25))['sessions'], [])

    def test_rir_raises_two_steps_or_holds(self):
        def hist(rir):
            return [{'id': 1, 'sessionId': '2026-11-20', 'exercise': 'Marklyft', 'sets': 3, 'reps': '5',
                     'weight': 90.0, 'note': '', 'rir': rir, 'date': '2026-11-20'}]
        def weight(rir):
            prog = strength_program.program('heavy', hist(rir), date(2026, 11, 25))
            return next(e for e in prog['sessions'][1]['exercises'] if e['exercise'] == 'Marklyft')['weight']
        self.assertEqual(weight(None), 92.5)
        self.assertEqual(weight(3), 95.0)
        self.assertEqual(weight(0), 90.0)


class WeeklyReportTests(unittest.TestCase):
    def test_report_against_frame(self):
        weeks = season_plan.build(RACE, 'half', 54, 18, 48)
        acts = [{'type': 'running', 'distance': 10000, 'date': '2026-11-23', 'raw': {'activityTrainingLoad': 100}},
                {'type': 'running', 'distance': 40000, 'date': '2026-11-29', 'raw': {}},
                {'type': 'ice_hockey', 'distance': 0, 'date': '2026-11-26', 'raw': {'duration': 7200}}]
        sessions = [{'type': 'run', 'title': 'Tröskel', 'status': 'completed',
                     'execution': {'repVerdict': 'on_target'}}]
        r = weekly_report.build(weeks[0], weeks[1], acts, sessions, [], {'2026-11-24'})
        self.assertEqual(r['verdict'], 'on_target')
        self.assertEqual(r['headline'], 'Veckan i fas med planen')
        self.assertIn('ice hockey', ' '.join(r['lines']))
        self.assertIn('Nästa vecka', r['advice'][-1])
        self.assertLessEqual(len(r['push']['body']), 230)


if __name__ == '__main__':
    unittest.main()
