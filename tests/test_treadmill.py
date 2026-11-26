import os
import unittest
from unittest.mock import patch

from werkzeug.security import generate_password_hash

os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402
import session_analysis  # noqa: E402
import treadmill  # noqa: E402
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402

# 2025-11-25: 2 km uppv, 4 × 2 km i 15 km/h med 90 s ståvila, nedjogg.
# Klockan (handleden) gav dragen 3:46–3:50/km.
SPLITS = {'lapDTOs': [
    {'distance': 2000, 'duration': 660, 'averageSpeed': 3.03, 'averageHR': 135},
    {'distance': 2125, 'duration': 480, 'averageSpeed': 4.42, 'averageHR': 163},
    {'distance': 20, 'duration': 90, 'averageSpeed': 0.2, 'averageHR': 140},
    {'distance': 2086, 'duration': 480, 'averageSpeed': 4.35, 'averageHR': 167},
    {'distance': 20, 'duration': 90, 'averageSpeed': 0.2, 'averageHR': 142},
    {'distance': 2100, 'duration': 480, 'averageSpeed': 4.37, 'averageHR': 169},
    {'distance': 20, 'duration': 90, 'averageSpeed': 0.2, 'averageHR': 144},
    {'distance': 2104, 'duration': 480, 'averageSpeed': 4.38, 'averageHR': 170},
    {'distance': 1500, 'duration': 540, 'averageSpeed': 2.78, 'averageHR': 150},
]}
PLANNED = {'type': 'run', 'km': 12, 'title': 'Tröskel 4×2 km (band) ~12 km',
           'detail': '2 km uppv, 4 × 2 km i 4:00/km (15 km/h) med 90 s ståvila'}
ACTIVITY = {'activityId': 7, 'activityType': {'typeKey': 'treadmill_running'},
            'distance': 11000, 'duration': 2950, 'averageHR': 158}


class ParseSpeedTests(unittest.TestCase):
    def test_accepts_kmh_comma_and_pace(self):
        self.assertEqual(treadmill.parse_speed('15'), 15.0)
        self.assertEqual(treadmill.parse_speed('15,1 km/h'), 15.1)
        self.assertEqual(treadmill.parse_speed('4:00'), 15.0)
        self.assertIsNone(treadmill.parse_speed(' '))

    def test_rejects_nonsense(self):
        for value in ('abc', '45', '1', True):
            with self.assertRaises(ValueError):
                treadmill.parse_speed(value)

    def test_clean_speeds_drops_blank_laps(self):
        self.assertEqual(treadmill.clean_speeds({'laps': {'1': '15', '2': ''}}), {'1': 15.0})
        with self.assertRaises(ValueError):
            treadmill.clean_speeds({'laps': {'x': '15'}})


class TreadmillAnalysisTests(unittest.TestCase):
    def laps(self):
        return session_analysis.normalize_laps(SPLITS)

    def test_without_belt_speeds_pace_is_not_judged(self):
        analysis = session_analysis.analyze_run(ACTIVITY, self.laps(), PLANNED)
        self.assertEqual(analysis['paceSource'], 'treadmill_unknown')
        self.assertEqual(analysis['flags'][0], 'treadmill_speed_missing')
        self.assertIsNone(analysis['repVerdict'])
        self.assertIsNone(analysis['fadePct'])
        self.assertIsNone(analysis['avgPace'])
        self.assertTrue(all(rep['pace'] is None for rep in analysis['reps']))
        self.assertEqual(session_analysis.headline_for(analysis), 'Ange bandets fart')
        self.assertIn('NOT reliable', session_analysis.describe_run(analysis))

    def test_belt_speeds_make_reps_exact(self):
        speeds = {1: 15.0, 3: 15.0, 5: 15.0, 7: 15.0, 0: 11.0, 8: 10.0}
        laps = treadmill.apply_to_laps(self.laps(), speeds)
        activity = dict(ACTIVITY, distance=treadmill.corrected_distance_m(laps))
        analysis = session_analysis.analyze_run(activity, laps, PLANNED)
        self.assertEqual(analysis['paceSource'], 'belt')
        self.assertEqual([rep['pace'] for rep in analysis['reps']], ['4:00/km'] * 4)
        self.assertEqual([rep['distanceM'] for rep in analysis['reps']], [2000] * 4)
        self.assertEqual(analysis['repVerdict'], 'on_target')
        self.assertEqual(analysis['fadePct'], 0.0)
        self.assertNotIn('reps_above_target_pace', analysis['flags'])
        self.assertIn('15 km/h belt', session_analysis.describe_run(analysis))

    def test_outdoor_run_is_untouched(self):
        outdoor = dict(ACTIVITY, activityType={'typeKey': 'running'})
        analysis = session_analysis.analyze_run(outdoor, self.laps(), PLANNED)
        self.assertEqual(analysis['paceSource'], 'watch')
        self.assertEqual(analysis['repVerdict'], 'too_fast')

    def test_detail_laps_and_overview_use_belt(self):
        detail = {'overview': {'distance': 11000, 'movingDuration': 960},
                  'laps': [{'duration': 480, 'distance': 2125, 'pace': 225.9},
                           {'duration': 480, 'distance': 2086, 'pace': 230.1}]}
        out = treadmill.apply_to_detail(detail, {0: 15.0, 1: 15.0})
        self.assertEqual(out['laps'][0]['pace'], 240.0)
        self.assertEqual(out['overview']['distance'], 4000.0)
        self.assertTrue(out['beltSpeeds'])
        self.assertEqual(detail['laps'][0]['pace'], 225.9)  # cachen rörs inte


class TreadmillApiTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()

    def login(self):
        return self.client.post('/api/login', json={
            'username': 'hugo', 'password': 'test-password'}).get_json()['csrfToken']

    def test_saves_speeds_and_recomputes_execution(self):
        csrf = self.login()
        raw = dict(ACTIVITY, startTimeLocal='2025-11-25 06:52:30')
        with patch.object(garmin_server, '_stored_activity_for_user', return_value=raw), \
             patch.object(garmin_server, 'set_cache') as set_cache, \
             patch.object(garmin_server, 'clear_cache'), \
             patch.object(garmin_server, '_refresh_executions_for_activity',
                          return_value=[{'id': 502, 'headline': 'Intervallerna på måltempo'}]) as refresh:
            response = self.client.put('/api/activities/7/treadmill',
                                       json={'laps': {'1': '15', '3': '4:00', '0': ''}},
                                       headers={'X-CSRF-Token': csrf})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['laps'], {'1': 15.0, '3': 15.0})
        key, value, user = set_cache.call_args[0]
        self.assertEqual((key, value['laps'], user), ('treadmill-speeds:v1:7', {'1': 15.0, '3': 15.0}, 1))
        refresh.assert_called_once()

    def test_rejects_outdoor_runs_and_bad_speeds(self):
        csrf = self.login()
        outdoor = dict(ACTIVITY, activityType={'typeKey': 'running'})
        with patch.object(garmin_server, '_stored_activity_for_user', return_value=outdoor):
            self.assertEqual(self.client.put('/api/activities/7/treadmill', json={'laps': {}},
                                             headers={'X-CSRF-Token': csrf}).status_code, 400)
        with patch.object(garmin_server, '_stored_activity_for_user', return_value=ACTIVITY):
            bad = self.client.put('/api/activities/7/treadmill', json={'laps': {'1': 'snabbt'}},
                                  headers={'X-CSRF-Token': csrf})
        self.assertEqual(bad.status_code, 400)

    def test_session_execution_uses_stored_belt_speeds(self):
        stored = ({'laps': {'1': 15, '3': 15, '5': 15, '7': 15}}, 0)
        with patch.object(garmin_server, 'get_cache', return_value=stored), \
             patch.object(garmin_server, '_run_activity_laps',
                          return_value=session_analysis.normalize_laps(SPLITS)):
            execution = garmin_server._build_session_execution(PLANNED, [ACTIVITY], None, 1, 'hugo')
        self.assertEqual(execution['paceSource'], 'belt')
        self.assertEqual(execution['repVerdict'], 'on_target')


if __name__ == '__main__':
    unittest.main()
