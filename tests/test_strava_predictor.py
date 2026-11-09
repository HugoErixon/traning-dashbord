"""Race predictor för användare som bara har Strava."""
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

import garmin_server  # noqa: E402
import race_predictor as rp  # noqa: E402
import strava_integration as si  # noqa: E402
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402

TODAY = date(2026, 11, 8)


def km_lap(seconds, hr, max_hr=None, meters=1000):
    return {'distance': meters, 'moving_time': seconds, 'elapsed_time': seconds,
            'average_heartrate': hr, 'max_heartrate': max_hr or hr + 5}


def speed_at(hr):
    return 2.8 + 0.035 * (hr - 140)


def strava_run(day, hrs, max_hr=195, race=False):
    """Ett pass med automatiska kilometervarv, i race predictorns form efter lap_rows."""
    laps = []
    for hr in hrs:
        seconds = round(1000 / speed_at(hr))
        laps += [km_lap(seconds, hr)] * 2
    return {'id': hash((day, tuple(hrs))), 'date': day.isoformat(), 'type': 'running', 'name': 'Pass',
            'distance': 1000 * len(laps), 'duration': sum(l['moving_time'] for l in laps), 'avg_hr': 150,
            'raw': {'maxHR': max_hr, 'eventType': {'typeKey': 'race' if race else 'uncategorized'}},
            'laps': si.lap_rows(laps)}


class LapRowsTests(unittest.TestCase):
    def test_auto_laps_merge_into_six_minute_chunks(self):
        rows = si.lap_rows([km_lap(250, 150)] * 6)
        self.assertEqual(len(rows), 3)
        seconds, meters, hr, speed, kind, max_hr = rows[0]
        self.assertEqual((seconds, meters, hr, kind, max_hr), (500.0, 2000.0, 150.0, 'ACTIVE', 155))
        self.assertAlmostEqual(speed, 4.0)

    def test_a_fast_kilometer_is_not_merged_with_a_slow_one(self):
        rows = si.lap_rows([km_lap(300, 140), km_lap(220, 175), km_lap(300, 140)])
        self.assertEqual([r[0] for r in rows], [300.0, 220.0, 300.0])

    def test_manual_interval_laps_are_kept_as_reps(self):
        rows = si.lap_rows([km_lap(600, 145, meters=2100), km_lap(170, 178, meters=800),
                            km_lap(120, 150, meters=300)])
        self.assertEqual([r[1] for r in rows], [2100.0, 800.0, 300.0])

    def test_strava_race_flag_becomes_event_type(self):
        summary = si.normalize_summary({'id': 1, 'sport_type': 'Run', 'workout_type': 1})
        self.assertEqual(summary['eventType']['typeKey'], 'race')
        self.assertEqual(si.normalize_summary({'id': 2, 'sport_type': 'Run'})['eventType']['typeKey'],
                         'uncategorized')


class EstimatedThresholdTests(unittest.TestCase):
    def test_ninety_percent_of_credible_max_skipping_a_spike(self):
        runs = [strava_run(TODAY - timedelta(days=d), [150], max_hr=hr)
                for d, hr in ((1, 230), (3, 220), (5, 198), (9, 196))]
        # 230 ligger utanför rimligt spann, 220 sticker ut mer än fem slag mot 198.
        self.assertEqual(rp.estimate_lthr(runs, TODAY), {'lthr': 178, 'maxHr': 198})

    def test_too_few_runs_with_heart_rate(self):
        self.assertIsNone(rp.estimate_lthr([strava_run(TODAY, [150], max_hr=200)], TODAY))

    def test_strava_runs_give_a_prediction_with_declared_sources(self):
        runs = [strava_run(TODAY - timedelta(days=d), (140, 150, 160, 172, 174)) for d in range(0, 40, 3)]
        estimate = rp.estimate_lthr(runs, TODAY)
        result = rp.predictor([], [], runs, TODAY, lthr_override=estimate['lthr'], source='strava',
                              lthr_source='estimated', max_hr=estimate['maxHr'])
        self.assertTrue(result['available'], result.get('reason'))
        self.assertEqual(result['source'], 'strava')
        self.assertEqual(result['model']['lthrSource'], 'estimated')
        self.assertEqual(result['model']['lthr'], round(195 * 0.9))
        self.assertIn('Strava ger ingen terrängjusterad fart', result['method'])
        # Linjen vid 97 % av 176 slag: konstruerad löpare ger exakt den farten.
        self.assertAlmostEqual(result['model']['paceSec'], 1000 / speed_at(176 * 0.97), delta=2)


class StravaServerTests(unittest.TestCase):
    def setUp(self):
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()

    def test_garmin_wins_when_both_are_connected(self):
        with mock.patch.object(garmin_server, '_garmin_connected', return_value=True), \
             mock.patch.object(garmin_server, '_strava_connected', return_value=True):
            self.assertEqual(garmin_server._race_source(1, 'hugo'), 'garmin')

    def test_strava_only_user_and_unconnected_user(self):
        with mock.patch.object(garmin_server, '_garmin_connected', return_value=False), \
             mock.patch.object(garmin_server, '_has_garmin_runs', return_value=False):
            with mock.patch.object(garmin_server, '_strava_connected', return_value=True):
                self.assertEqual(garmin_server._race_source(1, 'hugo'), 'strava')
            with mock.patch.object(garmin_server, '_strava_connected', return_value=False):
                self.assertIsNone(garmin_server._race_source(1, 'hugo'))
                result = garmin_server._race_prediction(1, 'hugo', allow_fetch=False)
                self.assertFalse(result['available'])
                self.assertIn('Koppla Garmin eller Strava', result['reason'])

    def test_history_fetches_only_what_is_new_and_merges(self):
        old = {'activityId': 1, 'startTimeLocal': (date.today() - timedelta(days=10)).isoformat() + 'T08:00:00Z',
               'activityType': {'typeKey': 'running'}}
        new_run = {'id': 2, 'sport_type': 'Run', 'start_date_local': date.today().isoformat() + 'T07:00:00Z'}
        with mock.patch.object(garmin_server, 'get_cache', return_value=([old], 0)), \
             mock.patch.object(garmin_server, 'set_cache') as save, \
             mock.patch.object(garmin_server, '_strava_access_token', return_value='t'), \
             mock.patch.object(garmin_server.strava_integration, 'athlete_activities_since',
                               return_value=[new_run]) as fetch:
            history = garmin_server._strava_history('hugo', force=True)
        self.assertEqual([a['activityId'] for a in history], [1, 2])
        after = fetch.call_args.args[1]
        # Bara från senaste passet (minus två dagars marginal), inte hela året igen.
        self.assertLess(garmin_server.time.time() - after, 13 * 86400)
        self.assertEqual(save.call_args.args[0], garmin_server.STRAVA_HISTORY_KEY)

    def test_strava_prediction_uses_manual_threshold_over_estimate(self):
        runs = [strava_run(TODAY - timedelta(days=d), (140, 150, 160, 172, 174)) for d in range(0, 40, 3)]
        with mock.patch.object(garmin_server, '_strava_race_runs', return_value=runs), \
             mock.patch.object(garmin_server, '_threshold_hr', return_value=181), \
             mock.patch.object(garmin_server, '_race_target', return_value=None):
            result = garmin_server._strava_race_prediction(1, 'hugo', TODAY, '2026-03-01', allow_fetch=False)
        self.assertEqual(result['model']['lthr'], 181)
        self.assertEqual(result['model']['lthrSource'], 'manual')

    def test_lap_backfill_stops_at_the_rate_limit(self):
        runs = [{'id': i, 'type': 'running', 'avg_hr': 150, 'laps': None} for i in range(5)]
        calls = []

        def laps(token, activity_id):
            calls.append(activity_id)
            raise si.StravaRateLimited('429')
        with mock.patch.object(garmin_server, '_strava_access_token', return_value='t'), \
             mock.patch.object(garmin_server.strava_integration, 'activity_laps', side_effect=laps), \
             mock.patch.object(garmin_server, 'set_cache') as save:
            self.assertEqual(garmin_server._backfill_strava_laps(1, 'hugo', runs), 0)
        self.assertEqual(len(calls), 1)
        save.assert_not_called()


class ThresholdApiTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()
        login = self.client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})
        self.headers = {'X-CSRF-Token': login.get_json()['csrfToken']}

    def put(self, **extra):
        body = {'distance': 'half', 'seconds': 4920, **extra}
        with mock.patch.object(garmin_server, 'set_cache') as save, \
             mock.patch.object(garmin_server, '_store_race_prediction', return_value={'available': False}):
            response = self.client.put('/api/race-target', json=body, headers=self.headers)
        return response, {call.args[0]: call.args[1] for call in save.call_args_list}

    def test_threshold_is_saved_cleared_or_left_alone(self):
        response, saved = self.put(lthr='178')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(saved[garmin_server.THRESHOLD_HR_KEY], {'hr': 178})
        _, saved = self.put(lthr='')
        self.assertEqual(saved[garmin_server.THRESHOLD_HR_KEY], {'hr': None})
        _, saved = self.put()
        self.assertNotIn(garmin_server.THRESHOLD_HR_KEY, saved)

    def test_unrealistic_threshold_is_rejected(self):
        response, saved = self.put(lthr=260)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(saved, {})


if __name__ == '__main__':
    unittest.main()
