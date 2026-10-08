import os
import unittest
from datetime import date, timedelta
from unittest import mock

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import body_trends  # noqa: E402
import garmin_server  # noqa: E402


TODAY = date(2026, 10, 8)


def health(days, **fields):
    """One health_history row per day, oldest first; fields are callables of the offset."""
    rows = []
    for offset in range(days, -1, -1):
        row = {'date': (TODAY - timedelta(days=offset)).isoformat()}
        for name, value in fields.items():
            row[name] = value(offset) if callable(value) else value
        rows.append(row)
    return rows


def spec(key):
    return next(s for s in body_trends.TREND_SPECS if s[0] == key)


class DailyTrendTests(unittest.TestCase):
    def test_rising_hrv_compares_weekly_averages(self):
        rows = health(40, hrv_avg=lambda offset: 70 - offset * 0.5)
        result = body_trends.trend(spec('hrv'), rows, [], TODAY, 30)

        self.assertEqual(result['direction'], 'improving')
        self.assertAlmostEqual(result['now'], 68.5)    # snitt av offset 0..6
        self.assertAlmostEqual(result['then'], 56.5)   # snitt av offset 24..30
        self.assertEqual(result['thenDate'], (TODAY - timedelta(days=30)).isoformat())
        self.assertEqual(result['samples'], 31)

    def test_higher_resting_pulse_is_a_decline(self):
        rows = health(40, resting_hr=lambda offset: 55 - offset * 0.2)
        result = body_trends.trend(spec('rhr'), rows, [], TODAY, 30)
        self.assertEqual(result['direction'], 'declining')

    def test_change_inside_noise_is_stable(self):
        rows = health(40, hrv_avg=lambda offset: 60 + (offset % 2))
        result = body_trends.trend(spec('hrv'), rows, [], TODAY, 30)
        self.assertEqual(result['direction'], 'stable')

    def test_zero_readings_are_missing_not_measurements(self):
        rows = health(10, hrv_avg=lambda offset: 0 if offset % 2 else 60)
        result = body_trends.trend(spec('hrv'), rows, [], TODAY, 30)
        self.assertTrue(all(point['v'] == 60 for point in result['series']))

    def test_long_window_starts_where_history_starts(self):
        rows = health(50, hrv_avg=lambda offset: 70 - offset * 0.5)
        result = body_trends.trend(spec('hrv'), rows, [], TODAY, 180)

        self.assertEqual(result['thenDate'], (TODAY - timedelta(days=50)).isoformat())
        self.assertEqual(result['direction'], 'improving')

    def test_too_short_history_has_no_direction(self):
        rows = health(8, hrv_avg=lambda offset: 60 + offset)
        result = body_trends.trend(spec('hrv'), rows, [], TODAY, 30)
        self.assertIsNone(result['then'])
        self.assertEqual(result['direction'], 'unknown')

    def test_todays_partial_stress_is_ignored(self):
        rows = health(20, stress_avg=lambda offset: 90 if offset == 0 else 30)
        result = body_trends.trend(spec('stress'), rows, [], TODAY, 30)
        self.assertNotIn(TODAY.isoformat(), [p['t'] for p in result['series']])
        self.assertEqual(result['latest'], 30)

    def test_smooth_line_needs_three_nights(self):
        rows = health(1, hrv_avg=60)
        result = body_trends.trend(spec('hrv'), rows, [], TODAY, 30)
        self.assertEqual(result['smooth'], [])


class LevelTrendTests(unittest.TestCase):
    def test_vo2max_compares_with_value_before_window(self):
        metric_rows = [
            {'date': '2026-08-20', 'vo2max': 55.0},
            {'date': '2026-09-20', 'vo2max': 56.0},
            {'date': '2026-10-07', 'vo2max': 57.2},
        ]
        result = body_trends.trend(spec('vo2max'), [], metric_rows, TODAY, 30)

        self.assertEqual(result['then'], 55.0)
        self.assertEqual(result['thenDate'], '2026-08-20')
        self.assertEqual(result['now'], 57.2)
        self.assertEqual(result['direction'], 'improving')
        self.assertEqual([p['t'] for p in result['series']], ['2026-09-20', '2026-10-07'])

    def test_flickering_threshold_pace_is_averaged(self):
        # Garmin växlar mellan två värden dag för dag; det är snittet som rör sig.
        metric_rows = health(40, lactate_pace=lambda offset: (236.8, 240.0)[offset % 2] if offset > 15
                             else (240.0, 248.3)[offset % 2])
        result = body_trends.trend(spec('lt_pace'), [], metric_rows, TODAY, 30)
        self.assertEqual(result['direction'], 'declining')
        self.assertTrue(all(236.8 < p['v'] < 248.3 for p in result['smooth']))

    def test_single_reading_is_not_a_change(self):
        result = body_trends.trend(spec('vo2max'), [], [{'date': '2026-10-01', 'vo2max': 57}], TODAY, 30)
        self.assertIsNone(result['delta'])
        self.assertEqual(result['direction'], 'unknown')


class SleepTests(unittest.TestCase):
    def test_weekly_sleep_groups_by_iso_week(self):
        rows = health(9, sleep_hours=lambda offset: 8.0 if offset < 3 else 6.0, sleep_score=80)
        weeks = body_trends.weekly_sleep(rows, TODAY, 30)

        self.assertEqual([w['week'] for w in weeks], [40, 41])
        current = weeks[-1]
        self.assertTrue(current['current'])
        self.assertEqual(current['nights'], 4)  # måndag 5 okt – torsdag 8 okt
        self.assertEqual(current['nightsOnGoal'], 3)
        self.assertEqual(current['avgScore'], 80)

    def test_short_nights_are_compared_with_normal_nights(self):
        rows = health(30, sleep_hours=lambda offset: 6.0 if offset % 3 == 0 else 8.0,
                      hrv_avg=lambda offset: 50 if offset % 3 == 0 else 62,
                      resting_hr=lambda offset: 55 if offset % 3 == 0 else 51)
        result = body_trends.sleep_vs_recovery(rows, TODAY)

        self.assertTrue(result['enough'])
        self.assertEqual(result['hrvDiff'], -12.0)
        self.assertEqual(result['rhrDiff'], 4.0)

    def test_comparison_needs_enough_nights_in_both_groups(self):
        rows = health(30, sleep_hours=lambda offset: 6.0 if offset < 3 else 8.0, hrv_avg=60)
        result = body_trends.sleep_vs_recovery(rows, TODAY)
        self.assertFalse(result['enough'])
        self.assertIsNone(result['hrvDiff'])


class OverviewEndpointTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        self.client = garmin_server.app.test_client()
        self.client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})
        self.conn = mock.MagicMock()
        self.conn.__enter__.return_value = self.conn
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.cur.fetchall.side_effect = [
            health(40, hrv_avg=lambda offset: 70 - offset * 0.5, sleep_hours=7.8, sleep_score=85),
            [{'date': '2026-09-01', 'vo2max': 56.0}, {'date': '2026-10-07', 'vo2max': 57.2}],
        ]

        class FixedDate(date):
            @classmethod
            def today(cls):
                return TODAY

        for patch in (mock.patch.object(garmin_server, 'db', return_value=self.conn),
                      mock.patch.object(garmin_server, 'date', FixedDate)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_returns_trends_for_the_requested_window(self):
        response = self.client.get('/api/overview?days=30')
        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()

        self.assertEqual(data['windowDays'], 30)
        trends = {t['key']: t for t in data['trends']}
        self.assertEqual(trends['hrv']['direction'], 'improving')
        self.assertEqual(trends['vo2max']['now'], 57.2)
        self.assertIn('hrv', data['summary']['improving'])
        # Historiken hämtas längre bakåt än perioden, och bara för inloggad användare.
        (_, params), = [c.args for c in self.cur.execute.call_args_list
                        if 'health_history' in c.args[0]]
        self.assertEqual(params, ((TODAY - timedelta(days=150)).isoformat(), 1))

    def test_window_is_clamped(self):
        self.assertEqual(self.client.get('/api/overview?days=5000').get_json()['windowDays'], 365)

    def test_bad_window_is_rejected(self):
        response = self.client.get('/api/overview?days=abc')
        self.assertEqual(response.status_code, 400)

    def test_requires_login(self):
        response = garmin_server.app.test_client().get('/api/overview')
        self.assertEqual(response.status_code, 401)


if __name__ == '__main__':
    unittest.main()
