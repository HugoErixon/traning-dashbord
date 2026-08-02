import unittest
from datetime import date, timedelta

from training_analysis import execution_summary, metric, overview, weekly_volume


class MetricTrendTests(unittest.TestCase):
    def test_rising_hrv_is_an_improvement(self):
        series = [{'t': f'2026-07-{day:02d}', 'v': 50 + day}
                  for day in range(1, 8)]
        result = metric('hrv', 'HRV', 'ms', 0, 'up', 0.5, series)

        self.assertEqual(result['direction'], 'improving')
        self.assertGreater(result['slopePerWeek'], 0)

    def test_lower_threshold_pace_is_an_improvement(self):
        series = [{'t': f'2026-07-{day:02d}', 'v': 250 - day}
                  for day in range(1, 8)]
        result = metric('lt_pace', 'Tröskelfart', 'tempo', 'pace', 'down', 0.5, series)

        self.assertEqual(result['direction'], 'improving')
        self.assertLess(result['slopePerWeek'], 0)

    def test_small_changes_are_treated_as_measurement_noise(self):
        series = [{'t': f'2026-07-{day:02d}', 'v': 55 + day * 0.01}
                  for day in range(1, 8)]
        result = metric('hrv', 'HRV', 'ms', 0, 'up', 0.5, series)

        self.assertEqual(result['direction'], 'stable')


class VolumeTests(unittest.TestCase):
    def test_volume_counts_runs_but_not_cycling(self):
        today = date(2026, 8, 1)
        activities = [
            {'date': today.isoformat(), 'type': 'running', 'distance': 10000, 'raw': {}},
            {'date': (today - timedelta(days=2)).isoformat(), 'type': 'trail_running',
             'distance': 5000, 'raw': {}},
            {'date': today.isoformat(), 'type': 'cycling', 'distance': 40000, 'raw': {}},
        ]
        result = weekly_volume(activities, today=today)

        self.assertEqual(result['current7Km'], 15.0)
        self.assertEqual(result['current7Sessions'], 2)
        self.assertEqual(sum(week['km'] for week in result['weeks']), 15.0)


class ExecutionTests(unittest.TestCase):
    def test_execution_reports_adherence_and_quality_separately(self):
        sessions = [
            {'status': 'completed', 'execution': {'flags': []}},
            {'status': 'completed', 'execution': {'flags': ['easy_run_too_fast']}},
            {'status': 'missed', 'execution': None},
            {'status': 'skipped', 'execution': None},
        ]
        result = execution_summary(sessions)

        self.assertEqual(result['adherencePct'], 50)
        self.assertEqual(result['qualityPct'], 50)
        self.assertEqual(result['onTarget'], 1)

    def test_empty_plan_does_not_invent_percentages(self):
        result = execution_summary([])
        self.assertIsNone(result['adherencePct'])
        self.assertIsNone(result['qualityPct'])


class OverviewTests(unittest.TestCase):
    def test_low_adherence_becomes_a_priority(self):
        metrics = [
            {'key': 'hrv', 'label': 'HRV', 'samples': 10, 'direction': 'stable'},
            {'key': 'sleep', 'label': 'Sömnpoäng', 'samples': 10, 'direction': 'stable'},
        ]
        result = overview(metrics, {'delta7Pct': 0},
                          {'adherencePct': 40, 'qualityPct': None, 'evaluated': 0}, {})

        self.assertTrue(any(item['title'] == 'Skydda kontinuiteten'
                            for item in result['priorities']))

    def test_goal_gap_is_stated_plainly(self):
        goal = {'feasibility': {'verdict': 'out_of_reach', 'gapSec': 14}}
        result = overview([], {'delta7Pct': None},
                          {'adherencePct': None, 'qualityPct': None, 'evaluated': 0}, goal)

        self.assertEqual(result['priorities'][0]['title'], 'Stäng gapet till målfarten')
        self.assertIn('14 s/km', result['priorities'][0]['detail'])


if __name__ == '__main__':
    unittest.main()


class TrendBreakdownTests(unittest.TestCase):
    """The breakdown must always explain the number actually shown."""

    def metric(self, key, label, direction, samples=5):
        return {'key': key, 'label': label, 'direction': direction, 'samples': samples}

    def test_entries_sum_to_the_raw_score(self):
        metrics = [
            self.metric('hrv', 'HRV', 'improving'),
            self.metric('sleep', 'Sömnpoäng', 'improving'),
            self.metric('rhr', 'Vilopuls', 'declining'),
        ]
        result = overview(metrics, {}, {'adherencePct': 90, 'qualityPct': 40, 'evaluated': 5})

        deltas = sum(e['delta'] for e in result['breakdown'] if e.get('delta'))
        self.assertEqual(result['base'] + deltas, result['rawScore'])
        self.assertEqual(result['score'], result['rawScore'])

    def test_clamped_scores_say_so(self):
        metrics = [self.metric(k, k, 'declining') for k in
                   ('hrv', 'rhr', 'sleep', 'vo2max', 'endurance', 'lt_pace')]
        result = overview(metrics, {}, {'adherencePct': 10, 'qualityPct': 10, 'evaluated': 5})

        self.assertEqual(result['score'], 20)
        self.assertLess(result['rawScore'], 20)
        clamp = next(e for e in result['breakdown'] if e['key'] == 'clamp')
        self.assertIn('20', clamp['label'])

    def test_missing_execution_data_is_stated_not_hidden(self):
        result = overview([], {}, {})
        keys = {e['key']: e for e in result['breakdown']}

        self.assertIsNone(keys['adherence']['delta'])
        self.assertIsNone(keys['quality']['delta'])
        self.assertIn('saknas', keys['adherence']['label'].lower())

    def test_a_flat_period_scores_the_base_and_explains_why(self):
        result = overview([], {}, {})

        self.assertEqual(result['score'], result['base'])
        self.assertEqual(result['breakdown'][0]['key'], 'base')

    def test_stable_markers_are_listed_with_no_effect(self):
        metrics = [self.metric('hrv', 'HRV', 'stable'),
                   self.metric('rhr', 'Vilopuls', 'stable')]
        result = overview(metrics, {}, {})

        stable = next(e for e in result['breakdown'] if e['key'] == 'stable')
        self.assertEqual(stable['delta'], 0)
        self.assertIn('HRV', stable['detail'])

    def test_every_entry_carries_a_label_and_a_reason(self):
        metrics = [self.metric('hrv', 'HRV', 'improving'),
                   self.metric('rhr', 'Vilopuls', 'declining')]
        result = overview(metrics, {}, {'adherencePct': 70, 'qualityPct': 60, 'evaluated': 3})

        for entry in result['breakdown']:
            self.assertTrue(entry['label'])
            self.assertTrue(entry['detail'])
            self.assertIn(entry['tone'], {'good', 'warn', 'neutral'})
