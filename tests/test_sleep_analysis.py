import unittest

from sleep_analysis import (
    bedtime_consistency,
    format_clock,
    format_hours,
    goal_streak,
    sleep_debt,
    stage_balance,
    summarize,
    trend,
)


def night(date, hours=None, score=None, start=None, deep=None, rem=None):
    return {'date': date, 'sleep_hours': hours, 'sleep_score': score,
            'sleep_start': start, 'deep_pct': deep, 'rem_pct': rem}


class FormattingTests(unittest.TestCase):
    def test_hours_render_as_hours_and_minutes(self):
        self.assertEqual(format_hours(7.5), '7 h 30 min')
        self.assertEqual(format_hours(0.75), '45 min')

    def test_clock_wraps_past_midnight(self):
        self.assertEqual(format_clock(1470), '00:30')


class DebtTests(unittest.TestCase):
    def test_debt_is_the_shortfall_against_the_target(self):
        nights = [night(f'2026-07-{d}', hours=7.0) for d in range(25, 32)]
        result = sleep_debt(nights)

        self.assertEqual(result['nights'], 7)
        self.assertAlmostEqual(result['debtH'], 3.5, places=2)
        self.assertEqual(result['surplusH'], 0)

    def test_sleeping_past_the_target_produces_surplus_not_negative_debt(self):
        nights = [night(f'2026-07-{d}', hours=8.0) for d in range(25, 32)]
        result = sleep_debt(nights)

        self.assertEqual(result['debtH'], 0)
        self.assertAlmostEqual(result['surplusH'], 3.5, places=2)

    def test_missing_nights_neither_add_nor_forgive_debt(self):
        nights = [night('2026-07-31', hours=6.5), night('2026-07-30'),
                  night('2026-07-29', hours=6.5)]
        result = sleep_debt(nights)

        self.assertEqual(result['nights'], 2)
        self.assertAlmostEqual(result['debtH'], 2.0, places=2)


class ConsistencyTests(unittest.TestCase):
    def test_bedtimes_either_side_of_midnight_are_close_together(self):
        # 23:40 and 00:20 are forty minutes apart, not twenty-three hours.
        nights = [night('2026-07-31', start='2026-07-31 23:40'),
                  night('2026-07-30', start='2026-07-31 00:20'),
                  night('2026-07-29', start='2026-07-30 00:00')]
        result = bedtime_consistency(nights)

        self.assertEqual(result['verdict'], 'steady')
        self.assertLess(result['spreadMin'], 30)

    def test_wandering_bedtimes_read_as_irregular(self):
        nights = [night('2026-07-31', start='2026-07-31 21:30'),
                  night('2026-07-30', start='2026-07-31 01:30'),
                  night('2026-07-29', start='2026-07-29 23:00'),
                  night('2026-07-28', start='2026-07-29 02:00')]
        result = bedtime_consistency(nights)

        self.assertEqual(result['verdict'], 'irregular')

    def test_too_few_nights_gives_no_verdict(self):
        self.assertIsNone(bedtime_consistency([night('2026-07-31', start='23:00')]))


class StageTests(unittest.TestCase):
    def test_low_deep_and_rem_are_flagged(self):
        result = stage_balance(9, 14)
        self.assertIn('deep_low', result['flags'])
        self.assertIn('rem_low', result['flags'])

    def test_a_healthy_split_is_not_flagged(self):
        self.assertEqual(stage_balance(19, 22)['flags'], [])


class TrendTests(unittest.TestCase):
    def test_improving_scores_read_as_improving(self):
        # Index 0 is the most recent night, so the newest value is highest.
        nights = [night('d', score=s) for s in (85, 80, 75, 70, 65)]
        result = trend(nights, 'sleep_score')

        self.assertEqual(result['direction'], 'improving')
        self.assertGreater(result['perWeek'], 0)

    def test_declining_hours_read_as_declining(self):
        nights = [night('d', hours=h) for h in (6.0, 6.5, 7.0, 7.5, 8.0)]
        result = trend(nights, 'sleep_hours')

        self.assertEqual(result['direction'], 'declining')
        self.assertLess(result['perWeek'], 0)

    def test_flat_data_reads_as_stable(self):
        nights = [night('d', score=78) for _ in range(6)]
        self.assertEqual(trend(nights, 'sleep_score')['direction'], 'stable')

    def test_too_little_data_gives_no_trend(self):
        self.assertIsNone(trend([night('d', score=70)], 'sleep_score'))


class StreakTests(unittest.TestCase):
    def test_streak_counts_recent_nights_meeting_the_target(self):
        nights = [night('d', hours=h) for h in (8.0, 7.6, 7.5, 6.0, 8.0)]
        self.assertEqual(goal_streak(nights), 3)

    def test_a_missing_night_ends_the_streak(self):
        nights = [night('d', hours=8.0), night('d'), night('d', hours=8.0)]
        self.assertEqual(goal_streak(nights), 1)


class SummaryTests(unittest.TestCase):
    def test_summary_reports_best_and_worst_night(self):
        nights = [night('2026-07-31', hours=7.0, score=60),
                  night('2026-07-30', hours=8.0, score=91),
                  night('2026-07-29', hours=7.5, score=74)]
        result = summarize(nights)

        self.assertEqual(result['best']['score'], 91)
        self.assertEqual(result['worst']['score'], 60)
        self.assertEqual(result['targetH'], 7.5)

    def test_summary_survives_an_empty_history(self):
        result = summarize([])
        self.assertIsNone(result['debt'])
        self.assertIsNone(result['best'])
        self.assertEqual(result['streak'], 0)


if __name__ == '__main__':
    unittest.main()
