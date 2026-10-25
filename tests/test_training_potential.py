import math
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

import training_potential as tp
import garmin_server
from security import parse_users
from user_store import MemoryUserStore


TODAY = date(2026, 10, 25)


def runs(days=200):
    return [{'date': TODAY - timedelta(days=i), 'type': 'running', 'distance': 5000}
            for i in range(1, days + 1)]


def metric(offset=1, value=55):
    return {'date': TODAY - timedelta(days=offset), 'vo2max': value}


def model_points():
    return [{'date': (TODAY - timedelta(weeks=24 - i)).isoformat(),
             'km': km, 'vo2max': 45 + 3 * math.log1p(km)}
            for i in range(24) for km in [30 + 20 * math.sin(i)]]


class PotentialModelTests(unittest.TestCase):
    def test_clear_association_must_beat_holdout_persistence(self):
        model = tp._model(model_points())
        self.assertTrue(model['available'])
        self.assertLess(model['mae'], model['persistenceMae'])

    def test_holdout_is_not_used_for_training(self):
        points = model_points()
        for point in points[-4:]:
            point['vo2max'] += 15
        self.assertFalse(tp._model(points)['available'])

    def test_flat_vo2_cannot_claim_trainability(self):
        points = [p | {'vo2max': 55} for p in model_points()]
        self.assertFalse(tp._model(points)['available'])

    def test_low_volume_variation_rejected(self):
        points = [p | {'km': 30 + i % 2} for i, p in enumerate(model_points())]
        self.assertFalse(tp._model(points)['available'])

    def test_negative_association_not_used_as_prescription(self):
        points = [p | {'vo2max': 110 - p['vo2max']} for p in model_points()]
        self.assertFalse(tp._model(points)['available'])

    def test_short_history_has_no_invented_response(self):
        result = tp.potential(runs(), [metric()], [], TODAY)
        self.assertFalse(result['model']['available'])
        self.assertEqual(result['mode'], 'hypothesis')
        self.assertEqual(result['projection'][-1]['vo2max'], 55)

    def test_empty_history_has_no_prediction_or_races(self):
        result = tp.potential([], [], [], TODAY, race_distance=5, race_seconds=1500)
        self.assertEqual(result['mode'], 'unavailable')
        self.assertEqual(result['projection'], [])
        self.assertEqual(result['races'], [])

    def test_stale_metrics_and_future_metrics_are_not_current(self):
        result = tp.potential(runs(), [metric(20), metric(-1, 70)], [], TODAY)
        self.assertEqual(result['mode'], 'unavailable')
        self.assertEqual(result['projection'], [])
        self.assertEqual(result['currentVo2max'], 55)

    def test_only_running_before_today_counts_and_units_are_km(self):
        activities = runs(28) + [
            {'date': TODAY, 'type': 'running', 'distance': 100000},
            {'date': TODAY - timedelta(days=1), 'type': 'cycling', 'distance': 100000},
            {'date': TODAY - timedelta(days=1), 'type': 'running', 'distance': float('nan')},
        ]
        result = tp.potential(activities, [metric()], [], TODAY)
        self.assertEqual(result['baselineKm'], 35)
        self.assertEqual(result['runningActivities'], 28)

    def test_current_vo2_uses_recent_median_not_single_outlier(self):
        result = tp.potential(runs(), [metric(0, 65), metric(1, 55), metric(2, 55)], [], TODAY)
        self.assertEqual(result['currentVo2max'], 55)

    def test_bad_values_do_not_generate_nan_json(self):
        result = tp.potential(runs(), [metric(0, 'NaN'), metric(1, 'inf'), metric(2, 55),
                                      {'date': 'bad', 'vo2max': 60}], [], TODAY)
        self.assertEqual(result['currentVo2max'], 55)
        self.assertIsNone(tp.number('NaN'))

    def test_hypothesis_is_defined_at_twelve_weeks_and_independent_of_volume(self):
        result = tp.potential(runs(), [metric()], [], TODAY, assumed_gain=5, weekly_km=40)
        other = tp.potential(runs(), [metric()], [], TODAY, assumed_gain=5, weekly_km=100)
        self.assertEqual(result['projection'], other['projection'])
        self.assertEqual(result['projection'][-1]['vo2max'], 57.75)
        self.assertTrue(other['warnings'])

    def test_decline_and_longer_horizon_are_supported(self):
        result = tp.potential(runs(), [metric()], [], TODAY, assumed_gain=-5, weeks=26)
        self.assertLess(result['projection'][-1]['vo2max'], 52.25)
        for point in result['projection']:
            self.assertLessEqual(point['low'], point['vo2max'])
            self.assertGreaterEqual(point['high'], point['vo2max'])

    def test_extrapolation_does_not_silently_become_a_prediction(self):
        with mock.patch.object(tp, '_model', return_value=tp._model(model_points())):
            result = tp.potential(runs(), [metric()], [], TODAY, weekly_km=100)
        self.assertEqual(result['mode'], 'unavailable')
        self.assertEqual(result['projection'], [])

    def test_in_range_model_starts_at_current_level(self):
        with mock.patch.object(tp, '_model', return_value=tp._model(model_points())):
            result = tp.potential(runs(), [metric()], [], TODAY, weekly_km=40)
        self.assertEqual(result['mode'], 'history')
        self.assertEqual(result['projection'][0]['vo2max'], 55)
        self.assertGreater(result['projection'][-1]['vo2max'], 55)

    def test_partial_weeks_and_missing_vo2_are_not_filled_in(self):
        result = tp.potential(runs(), [metric(1), metric(10), metric(31)], [], TODAY)
        self.assertEqual(len(result['history']), 2)

    def test_recovery_is_context_not_a_causal_multiplier(self):
        health = [{'date': TODAY - timedelta(days=i), 'sleep_hours': 8, 'hrv_avg': 60}
                  for i in range(1, 8)]
        result = tp.potential(runs(), [metric()], health, TODAY, assumed_gain=3)
        no_health = tp.potential(runs(), [metric()], [], TODAY, assumed_gain=3)
        self.assertEqual(result['projection'], no_health['projection'])
        self.assertEqual(result['recovery']['sleep_hours'], 8)

    def test_race_reference_is_preserved_at_same_distance_without_change(self):
        result = tp.potential(runs(), [metric()], [], TODAY, race_distance=5, race_seconds=1200)
        self.assertEqual(result['races'][0]['currentSeconds'], 1200)
        self.assertEqual(result['races'][0]['seconds'], 1200)
        self.assertEqual(len(result['races']), 4)
        self.assertGreater(result['races'][-1]['highSeconds'], result['races'][-1]['lowSeconds'])

    def test_shorter_distance_extrapolation_keeps_bounds_ordered(self):
        result = tp.potential(runs(), [metric()], [], TODAY, assumed_gain=3,
                              race_distance=42.195, race_seconds=14400)
        for race in result['races']:
            self.assertLessEqual(race['lowSeconds'], race['seconds'])
            self.assertGreaterEqual(race['highSeconds'], race['seconds'])
            self.assertLess(race['seconds'], race['currentSeconds'])


class PotentialEndpointTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()
        self.client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})
        self.conn = mock.MagicMock()
        self.conn.__enter__.return_value = self.conn
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.cur.fetchall.side_effect = [runs(), [metric()], []]
        patch = mock.patch.object(garmin_server, 'db', return_value=self.conn)
        self.db_mock = patch.start()
        self.addCleanup(patch.stop)

    def test_auth_required(self):
        response = garmin_server.app.test_client().get('/api/potential')
        self.assertEqual(response.status_code, 401)
        self.db_mock.assert_not_called()

    def test_inputs_reject_nonfinite_partial_or_out_of_range_values(self):
        for query in ['weeks=nan', 'weeks=27', 'weeks=3', 'weekly_km=NaN', 'weekly_km=-1',
                      'weekly_km=201', 'gain=inf', 'gain=11', 'race_distance=5',
                      'race_seconds=1200', 'race_seconds=NaN&race_distance=5',
                      'race_seconds=1200&race_distance=6', 'race_seconds=5&race_distance=5']:
            with self.subTest(query=query):
                self.assertEqual(self.client.get('/api/potential?' + query).status_code, 400)
        self.db_mock.assert_not_called()

    def test_only_current_users_data_is_read_and_nothing_written(self):
        with mock.patch.object(garmin_server, 'uid', return_value=27):
            response = self.client.get('/api/potential?weeks=16&weekly_km=40&gain=3')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['weeks'], 16)
        self.assertEqual(response.json['assumedGain'], 3)
        for call in self.cur.execute.call_args_list:
            sql, params = call.args
            self.assertTrue(sql.startswith('SELECT'))
            self.assertIn('user_id=%s', sql)
            self.assertEqual(params[0], 27)

    def test_database_failure_does_not_return_fake_results(self):
        self.db_mock.side_effect = RuntimeError('test outage')
        response = self.client.get('/api/potential')
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('projection', response.json)


if __name__ == '__main__':
    unittest.main()
