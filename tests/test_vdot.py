import unittest
from datetime import date

import vdot
from pace_progression import describe_anchor, goal_feasibility, target_band, validate_proposal


class FormulaTests(unittest.TestCase):
    def test_matches_daniels_table_for_vdot_50(self):
        # Daniels' Running Formula, VDOT 50: 5 km 19:57, HM 1:31:35,
        # T 4:15/km, I 3:55/km.
        self.assertAlmostEqual(vdot.from_race(5, 19 * 60 + 57), 50.0, delta=0.1)
        self.assertAlmostEqual(vdot.race_time(50, 5), 1197, delta=5)
        self.assertAlmostEqual(vdot.race_time(50, vdot.HALF_KM), 5495, delta=15)
        zones = vdot.paces(50)
        self.assertEqual(zones['T']['pace'], '4:15/km')
        self.assertEqual(zones['I']['pace'], '3:55/km')

    def test_race_time_is_the_inverse_of_from_race(self):
        for value in (35, 45, 56.3, 70):
            seconds = vdot.race_time(value, 10)
            self.assertAlmostEqual(vdot.from_race(10, seconds), value, delta=0.1)

    def test_zones_are_ordered_from_easy_to_fastest(self):
        z = vdot.paces(56)
        self.assertGreater(z['E']['paceSec'], z['M']['paceSec'])
        self.assertGreater(z['M']['paceSec'], z['HM']['paceSec'])
        self.assertGreater(z['HM']['paceSec'], z['T']['paceSec'])
        self.assertGreater(z['T']['paceSec'], z['I']['paceSec'])
        self.assertEqual(z['I']['paceSec'] - z['R']['paceSec'], 15)

    def test_threshold_pace_gives_back_its_vdot(self):
        value = vdot.from_threshold_pace(vdot.paces(52)['T']['paceSec'])
        self.assertAlmostEqual(value, 52, delta=0.3)

    def test_implausible_input_gives_nothing(self):
        self.assertIsNone(vdot.from_race(0.4, 60))
        self.assertIsNone(vdot.from_race(5, 'x'))
        self.assertIsNone(vdot.paces(120))
        self.assertIsNone(vdot.from_threshold_pace(60))


PREDICTION = {'available': True, 'predictions': [
    {'key': 'half', 'seconds': 4955, 'time': '1:22:35'}]}


class CurrentTests(unittest.TestCase):
    def test_predictor_is_preferred_over_threshold(self):
        info = vdot.current(PREDICTION, lt_pace_sec=242)
        self.assertEqual(info['source'], 'predictor')
        self.assertAlmostEqual(info['vdot'], 56.3, delta=0.1)
        self.assertEqual(info['alternatives'][0]['source'], 'threshold')

    def test_manual_value_wins(self):
        info = vdot.current(PREDICTION, manual=58)
        self.assertEqual(info['source'], 'manual')
        self.assertEqual(info['vdot'], 58)

    def test_old_races_are_ignored(self):
        races = [{'date': date(2026, 5, 16), 'km': 21.1, 'seconds': 5205, 'name': 'Varvet'}]
        info = vdot.current(None, races=races, today=date(2026, 11, 20))
        self.assertFalse(info['available'])
        info = vdot.current(None, races=races, today=date(2026, 6, 1))
        self.assertEqual(info['source'], 'race')

    def test_nothing_known(self):
        self.assertFalse(vdot.current()['available'])


class PaceProgressionTests(unittest.TestCase):
    def test_bands_come_from_vdot_zones(self):
        band = target_band('threshold', 240, vdot=56.3)
        self.assertEqual(band['zone'], 'T')
        self.assertEqual(band['text'], vdot.paces(56.3)['T']['text'])
        self.assertEqual(target_band('easy', 240, vdot=56.3)['zone'], 'E')

    def test_without_vdot_threshold_offsets_remain(self):
        self.assertNotIn('zone', target_band('threshold', 240))

    def test_proposal_validated_against_interval_zone(self):
        verdict = validate_proposal('interval', 240, None, vdot=56.3)
        self.assertNotEqual(verdict['status'], 'accepted')
        self.assertLessEqual(verdict['paceSec'], vdot.paces(56.3)['I']['highSec'])

    def test_goal_compared_with_half_marathon_pace(self):
        # 1:22 is 3:53/km; VDOT 56.3 runs a half at 3:55/km → a stretch, not reached.
        result = goal_feasibility(233, None, vdot=56.3)
        self.assertEqual(result['currentCapablePace'], vdot.paces(56.3)['HM']['pace'])
        self.assertEqual(result['verdict'], 'stretch')

    def test_prompt_lists_every_zone(self):
        info = vdot.current(PREDICTION)
        text = describe_anchor({'ltPaceSec': 242, 'ltPace': '4:02/km'}, None, info)
        for marker in ('VDOT 56.3', 'E (easy', 'T (threshold', 'I (VO2max', 'R (repetitions'):
            self.assertIn(marker, text)


if __name__ == '__main__':
    unittest.main()
