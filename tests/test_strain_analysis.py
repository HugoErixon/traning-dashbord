import unittest
from datetime import date, timedelta

from strain_analysis import (
    consecutive_high_days, is_judgeable, reference_load, session_intensity,
    session_verdict, strain_balance, strain_from_load, strain_series, strain_summary,
)


TODAY = date(2026, 8, 2)


def activity(day, load, type_key='running', name='Pass', activity_id=1):
    return {
        'id': activity_id, 'name': name, 'date': day.isoformat(), 'type': type_key,
        'raw': {'activityTrainingLoad': load, 'activityType': {'typeKey': type_key}},
    }


class StrainCurveTests(unittest.TestCase):
    def test_an_average_day_scores_fifty(self):
        self.assertEqual(strain_from_load(100, reference=100), 50)

    def test_a_rest_day_scores_zero(self):
        self.assertEqual(strain_from_load(0, reference=100), 0)

    def test_the_curve_flattens_at_the_top(self):
        self.assertEqual(strain_from_load(250, reference=100), 90)
        self.assertEqual(strain_from_load(1000, reference=100), 100)

    def test_the_same_load_costs_less_for_a_fitter_athlete(self):
        beginner = strain_from_load(150, reference=60)
        seasoned = strain_from_load(150, reference=150)
        self.assertGreater(beginner, seasoned)

    def test_missing_reference_falls_back_without_crashing(self):
        self.assertGreater(strain_from_load(120, reference=None), 0)


class ReferenceLoadTests(unittest.TestCase):
    def test_garmin_chronic_wins_over_local_history(self):
        activities = [activity(TODAY, 300)]
        self.assertEqual(reference_load(activities, today=TODAY, chronic=88), 88)

    def test_history_is_averaged_across_the_whole_window(self):
        activities = [activity(TODAY - timedelta(days=n), 280) for n in range(4)]
        # 4 x 280 spread over 28 days, not over the 4 days that had sessions.
        self.assertAlmostEqual(reference_load(activities, today=TODAY), 40.0)

    def test_no_activities_gives_no_reference(self):
        self.assertIsNone(reference_load([], today=TODAY))


class SeriesTests(unittest.TestCase):
    def test_series_is_padded_with_rest_days_and_ends_today(self):
        series = strain_series([activity(TODAY, 100)], today=TODAY, days=5, reference=100)

        self.assertEqual(len(series), 5)
        self.assertEqual(series[-1]['t'], TODAY.isoformat())
        self.assertEqual(series[-1]['strain'], 50)
        self.assertEqual([point['strain'] for point in series[:-1]], [0, 0, 0, 0])

    def test_two_sessions_on_one_day_are_summed(self):
        activities = [activity(TODAY, 60, activity_id=1), activity(TODAY, 140, activity_id=2)]
        series = strain_series(activities, today=TODAY, days=2, reference=100)

        self.assertEqual(series[-1]['load'], 200.0)

    def test_consecutive_high_days_counts_back_from_today(self):
        series = [{'strain': 90}, {'strain': 20}, {'strain': 70}, {'strain': 80}]
        self.assertEqual(consecutive_high_days(series), 2)

    def test_a_light_day_breaks_the_streak(self):
        self.assertEqual(consecutive_high_days([{'strain': 90}, {'strain': 10}]), 0)


class BalanceTests(unittest.TestCase):
    def test_hard_day_on_low_readiness_is_overreaching(self):
        self.assertEqual(strain_balance(80, 30)['state'], 'overreaching')

    def test_hard_day_on_good_readiness_is_productive(self):
        self.assertEqual(strain_balance(80, 75)['state'], 'productive')

    def test_easy_day_on_high_readiness_leaves_room(self):
        self.assertEqual(strain_balance(10, 80)['state'], 'undertrained')

    def test_easy_day_on_low_readiness_is_recovery(self):
        self.assertEqual(strain_balance(10, 30)['state'], 'recovering')

    def test_without_readiness_the_state_is_unknown(self):
        self.assertEqual(strain_balance(80, None)['state'], 'unknown')


class SummaryTests(unittest.TestCase):
    def test_four_hard_days_in_a_row_is_flagged_over_everything_else(self):
        activities = [activity(TODAY - timedelta(days=n), 200, activity_id=n)
                      for n in range(4)]
        summary = strain_summary(activities, readiness=80, chronic=100, today=TODAY)

        self.assertEqual(summary['consecutiveHighDays'], 4)
        self.assertEqual(summary['tone'], 'warn')
        self.assertIn('fjärde', summary['headline'].lower())

    def test_summary_reports_which_reference_it_used(self):
        activities = [activity(TODAY, 100)]
        self.assertEqual(
            strain_summary(activities, chronic=120, today=TODAY)['referenceSource'], 'garmin')
        self.assertEqual(
            strain_summary(activities, today=TODAY)['referenceSource'], 'history')

    def test_a_rest_day_after_hard_training_reads_as_recovery(self):
        activities = [activity(TODAY - timedelta(days=2), 250)]
        summary = strain_summary(activities, readiness=40, chronic=100, today=TODAY)

        self.assertEqual(summary['strain'], 0)
        self.assertEqual(summary['balance']['state'], 'recovering')


class SessionVerdictTests(unittest.TestCase):
    def test_a_hard_session_pushes_the_next_quality_session_two_days_out(self):
        verdict = session_verdict(activity(TODAY, 200), reference=100, today=TODAY)

        self.assertEqual(verdict['intensity'], 'hard')
        self.assertEqual(verdict['recoveryHours'], 48)
        self.assertEqual(verdict['nextQualityDate'], (TODAY + timedelta(days=2)).isoformat())

    def test_an_easy_session_does_not_block_tomorrow(self):
        verdict = session_verdict(activity(TODAY, 20), reference=100, today=TODAY)

        self.assertEqual(verdict['intensity'], 'easy')
        self.assertEqual(verdict['recoveryHours'], 0)
        self.assertIn('imorgon', verdict['timing'])

    def test_high_acwr_and_poor_sleep_stack_onto_the_recovery_window(self):
        verdict = session_verdict(activity(TODAY, 200), reference=100, acwr=1.5,
                                  sleep_hours=5.0, today=TODAY)

        self.assertEqual(verdict['recoveryHours'], 96)
        self.assertIn('high_acwr', verdict['flags'])
        self.assertIn('sleep_deficit', verdict['flags'])

    def test_the_recovery_window_is_capped(self):
        verdict = session_verdict(activity(TODAY, 400), reference=100, acwr=2.0,
                                  readiness=20, sleep_hours=4.0, today=TODAY)

        self.assertEqual(verdict['recoveryHours'], 96)

    def test_reasons_are_given_for_every_suggested_action(self):
        verdict = session_verdict(activity(TODAY, 200), reference=100, acwr=1.5, today=TODAY)

        self.assertTrue(verdict['recovery'])
        for action in verdict['recovery']:
            self.assertTrue(action['title'])
            self.assertTrue(action['why'])

    def test_a_calm_session_still_returns_one_action(self):
        verdict = session_verdict(activity(TODAY, 20), reference=100, today=TODAY)

        self.assertEqual(len(verdict['recovery']), 1)
        self.assertEqual(verdict['flags'], [])

    def test_activities_without_load_do_not_crash(self):
        bare = {'id': 9, 'name': 'Promenad', 'date': TODAY.isoformat(),
                'type': 'walking', 'raw': {}}
        verdict = session_verdict(bare, reference=100, today=TODAY)

        self.assertEqual(verdict['load'], 0)
        self.assertEqual(verdict['intensity'], 'easy')
        self.assertFalse(verdict['isRun'])

    def test_strength_sessions_are_scored_too(self):
        gym = activity(TODAY, 120, type_key='strength_training', name='Gym')
        verdict = session_verdict(gym, reference=100, today=TODAY)

        self.assertEqual(verdict['intensity'], 'moderate')
        self.assertFalse(verdict['isRun'])


class JudgeableTests(unittest.TestCase):
    def test_a_scored_session_is_worth_judging(self):
        self.assertTrue(is_judgeable(activity(TODAY, 80)))

    def test_a_watch_stopwatch_entry_is_not(self):
        # Garmin writes these as zero-length stop_watch activities; left in,
        # one outranks a real run when picking the most recent session.
        noise = {'id': 5, 'name': 'Timed Activity', 'date': TODAY.isoformat(),
                 'type': 'stop_watch', 'duration': 0, 'raw': {'duration': 0}}
        self.assertFalse(is_judgeable(noise))

    def test_a_long_session_without_load_still_counts(self):
        unscored = {'id': 6, 'name': 'Långpass', 'date': TODAY.isoformat(),
                    'type': 'running', 'duration': 2700, 'raw': {'duration': 2700}}
        self.assertTrue(is_judgeable(unscored))

    def test_a_few_seconds_without_load_does_not(self):
        blip = {'id': 7, 'name': 'Blip', 'date': TODAY.isoformat(),
                'type': 'running', 'duration': 45, 'raw': {'duration': 45}}
        self.assertFalse(is_judgeable(blip))


class IntensityTests(unittest.TestCase):
    def test_intensity_bands_ladder_upwards(self):
        self.assertEqual(session_intensity(10, 100), 'easy')
        self.assertEqual(session_intensity(70, 100), 'moderate')
        self.assertEqual(session_intensity(150, 100), 'hard')
        self.assertEqual(session_intensity(240, 100), 'very_hard')


if __name__ == '__main__':
    unittest.main()
