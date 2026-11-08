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
import training_insights as ti  # noqa: E402
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402

TODAY = date(2026, 11, 8)


def garmin_day(day, half):
    times = rp._all_from('half', half)
    return {'calendarDate': day.isoformat(), 'time5K': round(times['5k']),
            'time10K': round(times['10k']), 'timeHalfMarathon': half,
            'timeMarathon': round(times['marathon'])}


def garmin_series(days, start_half, end_half):
    return [garmin_day(TODAY - timedelta(days=days - i),
                       round(start_half + (end_half - start_half) * i / days))
            for i in range(days + 1)]


def threshold_rows(days, pace):
    return [{'date': (TODAY - timedelta(days=i)).isoformat(), 'lactate_pace': pace}
            for i in range(days)]


def race(day, km, seconds):
    return {'date': day.isoformat(), 'type': 'running', 'name': 'Varvet', 'distance': km * 1000,
            'duration': seconds, 'raw': {'eventType': {'typeKey': 'race'}}}


class RacePredictorTests(unittest.TestCase):
    def test_no_sources_means_no_prediction(self):
        result = rp.predictor([], [], [], TODAY)
        self.assertFalse(result['available'])

    def test_combines_garmin_and_threshold_equally(self):
        result = rp.predictor(garmin_series(30, 5200, 5200), threshold_rows(30, 240), [], TODAY)
        half = next(p for p in result['predictions'] if p['key'] == 'half')
        threshold_half = 240 * rp.KM['half'] * rp.THRESHOLD_TO_HALF
        self.assertAlmostEqual(half['seconds'], (5200 + threshold_half) / 2, delta=1)
        self.assertEqual({s['key'] for s in result['sources']}, {'garmin', 'threshold'})

    def test_threshold_alone_carries_prediction_without_garmin(self):
        result = rp.predictor([], threshold_rows(10, 240), [], TODAY)
        self.assertTrue(result['available'])
        self.assertEqual([s['key'] for s in result['sources']], ['threshold'])

    def test_stale_threshold_is_ignored(self):
        old = [{'date': (TODAY - timedelta(days=40)).isoformat(), 'lactate_pace': 240}]
        self.assertFalse(rp.predictor([], old, [], TODAY)['available'])

    def test_calibrates_against_own_race_and_caps_it(self):
        race_day = TODAY - timedelta(days=100)
        garmin = garmin_series(200, 5000, 5000)
        # Loppet gick 3 % långsammare än Garmin förutsade dagen innan.
        result = rp.predictor(garmin, [], [race(race_day, rp.KM['half'], 5150)], TODAY)
        source = result['sources'][0]
        self.assertAlmostEqual(source['calibration'], 1.03, places=3)
        self.assertAlmostEqual(result['predictions'][2]['seconds'], 5150, delta=2)
        wild = rp.predictor(garmin, [], [race(race_day, rp.KM['half'], 6500)], TODAY)
        self.assertEqual(wild['sources'][0]['calibration'], 1 + rp.CALIBRATION_LIMIT)

    def test_history_and_change_follow_the_data(self):
        result = rp.predictor(garmin_series(60, 5400, 5300), [], [], TODAY, history_days=60)
        self.assertEqual(result['history'][-1]['t'], TODAY.isoformat())
        self.assertLess(result['change28']['half'], 0)

    def test_target_gap_and_required_rate(self):
        target = {'distance': 'half', 'seconds': 4920, 'deadline': '2027-03-14'}
        result = rp.predictor(garmin_series(60, 5400, 5300), [], [], TODAY, target=target)
        t = result['target']
        self.assertEqual(t['gapSec'], 5300 - 4920)
        self.assertFalse(t['reached'])
        self.assertAlmostEqual(t['neededPerWeekSec'], 380 / t['weeksLeft'], places=0)
        self.assertGreater(t['recentPerWeekSec'], 0)


def run(day, label, km=10, seconds=3300, hr=150, speed=None, splits=None, event='uncategorized'):
    raw = {'trainingEffectLabel': label, 'eventType': {'typeKey': event}}
    if speed:
        raw['avgGradeAdjustedSpeed'] = speed
    if splits:
        raw['splitSummaries'] = splits
    return {'date': day.isoformat(), 'type': 'running', 'name': label, 'distance': km * 1000,
            'duration': seconds, 'avg_hr': hr, 'raw': raw}


def active(km, seconds, reps):
    return [{'splitType': 'INTERVAL_ACTIVE', 'distance': km * 1000, 'duration': seconds,
             'noOfSplits': reps}]


class TrainingInsightsTests(unittest.TestCase):
    def test_easy_efficiency_improves_with_lower_heart_rate(self):
        rows = [run(TODAY - timedelta(days=80), 'AEROBIC_BASE', hr=155, speed=2.8),
                run(TODAY - timedelta(days=70), 'AEROBIC_BASE', hr=154, speed=2.8),
                run(TODAY - timedelta(days=10), 'RECOVERY', hr=140, speed=2.8),
                run(TODAY - timedelta(days=5), 'AEROBIC_BASE', hr=141, speed=2.8)]
        easy = ti.insights(rows, TODAY)['areas'][0]
        self.assertEqual(easy['direction'], 'improving')
        self.assertGreater(easy['changePct'], 5)

    def test_short_reps_are_intervals_even_when_garmin_says_tempo(self):
        rows = [run(TODAY - timedelta(days=3), 'TEMPO', splits=active(8, 1840, 20)),
                run(TODAY - timedelta(days=2), 'LACTATE_THRESHOLD', splits=active(10.8, 2700, 3))]
        kinds = {s['name']: s['kind'] for s in ti.sessions(rows)}
        self.assertEqual(kinds, {'TEMPO': 'intervals', 'LACTATE_THRESHOLD': 'threshold'})

    def test_unstructured_hard_run_has_no_work_pace(self):
        rows = [run(TODAY - timedelta(days=3), 'VO2MAX', splits=active(2.7, 2400, 1))]
        self.assertNotIn('workPaceSec', ti.sessions(rows)[0])

    def test_races_are_kept_out_of_training_trends(self):
        rows = [run(TODAY - timedelta(days=3), 'VO2MAX', event='race', splits=active(21, 5200, 2))]
        result = ti.insights(rows, TODAY)
        self.assertTrue(all(a['count'] == 0 for a in result['areas']))
        self.assertEqual(result['weeks'][-1]['hard'], 1)

    def test_weekly_volume_counts_every_run(self):
        rows = [run(TODAY, None, km=2.5), run(TODAY, 'AEROBIC_BASE', km=8, speed=2.8)]
        self.assertEqual(ti.insights(rows, TODAY)['weeks'][-1]['km'], 10.5)


class RacePredictorApiTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()
        response = self.client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})
        self.csrf = response.get_json().get('csrfToken')

    def test_target_is_validated_before_saving(self):
        with mock.patch.object(garmin_server, 'set_cache') as save:
            response = self.client.put('/api/race-target', json={'distance': 'half', 'seconds': 5},
                                       headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 400)
        save.assert_not_called()

    def test_target_saved_and_prediction_returned(self):
        with mock.patch.object(garmin_server, 'set_cache') as save, \
             mock.patch.object(garmin_server, '_store_race_prediction', return_value={'available': False}):
            response = self.client.put('/api/race-target', json={
                'distance': 'half', 'seconds': 4920, 'deadline': '2027-03-14'},
                headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(save.call_args.args[1], {'distance': 'half', 'seconds': 4920,
                                                  'deadline': '2027-03-14'})

    def test_goal_block_carries_performance_lines(self):
        with mock.patch.object(garmin_server, 'get_user_goal', return_value=None), \
             mock.patch.object(garmin_server, '_performance_prompt_block', return_value='RACE PREDICTOR: x'):
            self.assertIn('RACE PREDICTOR: x', garmin_server._goal_prompt_block(1))
            self.assertNotIn('RACE PREDICTOR', garmin_server._goal_prompt_block(1, performance=False))


class RacePredictionCacheTests(unittest.TestCase):
    """Prognosen räknas bara om när ett nytt löppass har kommit in."""

    def setUp(self):
        garmin_server._race_refreshing.clear()

    def test_cached_prediction_is_served_while_marker_matches(self):
        stored = {'marker': '10:99', 'payload': {'available': True, 'today': 'x'}}
        with mock.patch.object(garmin_server, 'get_cache', return_value=(stored, 0)), \
             mock.patch.object(garmin_server, '_race_marker', return_value='10:99'), \
             mock.patch.object(garmin_server, '_race_prediction') as compute, \
             mock.patch.object(garmin_server, 'refresh_race_prediction_async') as refresh:
            result = garmin_server._cached_race_prediction(1, 'hugo')
        self.assertEqual(result, {'available': True, 'today': 'x'})
        compute.assert_not_called()
        refresh.assert_not_called()

    def test_new_run_starts_background_refresh_but_answers_at_once(self):
        stored = {'marker': '10:99', 'payload': {'available': True}}
        with mock.patch.object(garmin_server, 'get_cache', return_value=(stored, 0)), \
             mock.patch.object(garmin_server, '_race_marker', return_value='11:120'), \
             mock.patch.object(garmin_server, '_race_prediction') as compute, \
             mock.patch.object(garmin_server, 'refresh_race_prediction_async', return_value=True) as refresh:
            result = garmin_server._cached_race_prediction(1, 'hugo')
        self.assertTrue(result['refreshing'])
        compute.assert_not_called()
        refresh.assert_called_once_with(1, 'hugo')

    def test_first_visit_computes_and_stores(self):
        with mock.patch.object(garmin_server, 'get_cache', return_value=None), \
             mock.patch.object(garmin_server, '_store_race_prediction', return_value={'available': True}) as store:
            self.assertEqual(garmin_server._cached_race_prediction(1, 'hugo'), {'available': True})
        store.assert_called_once_with(1, 'hugo')

    def test_only_one_background_refresh_per_user(self):
        started = []
        with mock.patch.object(garmin_server.threading, 'Thread') as thread:
            thread.return_value.start.side_effect = lambda: started.append(1)
            self.assertTrue(garmin_server.refresh_race_prediction_async(1, 'hugo'))
            self.assertFalse(garmin_server.refresh_race_prediction_async(1, 'hugo'))
        self.assertEqual(len(started), 1)

    def test_synced_new_run_triggers_refresh(self):
        run = {'activityId': 5, 'activityType': {'typeKey': 'running'}}
        ride = {'activityId': 6, 'activityType': {'typeKey': 'cycling'}}
        for activities, expected in (([run], 1), ([ride], 0)):
            with mock.patch.object(garmin_server, 'APP_TESTING', False), \
                 mock.patch.object(garmin_server, '_unseen_activity_ids',
                                   return_value={a['activityId'] for a in activities}), \
                 mock.patch.object(garmin_server, 'save_activities'), \
                 mock.patch.object(garmin_server, 'record_session_verdicts'), \
                 mock.patch.object(garmin_server, 'refresh_race_prediction_async') as refresh:
                garmin_server.ingest_activities(activities, 1, announce=False)
            self.assertEqual(refresh.call_count, expected)
