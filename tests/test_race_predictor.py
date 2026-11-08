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


def lthr_rows(hr=180, days=60):
    return [{'date': (TODAY - timedelta(days=i)).isoformat(), 'lactate_hr': hr, 'lactate_pace': 240}
            for i in range(days)]


def speed_at(hr, base=2.8, per_beat=0.035):
    """Konstruerad löpare: 2,8 m/s vid 140 slag, +0,035 m/s per slag."""
    return base + per_beat * (hr - 140)


def training_run(day, hrs, name='Pass', race=False, minutes=8):
    laps = [[minutes * 60, speed_at(hr) * minutes * 60, hr, speed_at(hr), 'ACTIVE'] for hr in hrs]
    raw = {'eventType': {'typeKey': 'race' if race else 'uncategorized'}}
    return {'id': hash((day, name)), 'date': day.isoformat(), 'type': 'running', 'name': name,
            'distance': sum(l[1] for l in laps), 'duration': sum(l[0] for l in laps), 'raw': raw,
            'laps': laps}


def steady_block(days=40, step=3, hrs=(140, 150, 160, 172, 174)):
    return [training_run(TODAY - timedelta(days=d), hrs) for d in range(0, days, step)]


class RacePredictorTests(unittest.TestCase):
    def test_no_laps_means_no_prediction(self):
        result = rp.predictor([], lthr_rows(), [], TODAY)
        self.assertFalse(result['available'])
        self.assertIn('varv', result['reason'])

    def test_reads_the_runners_own_line_at_97_percent_of_threshold(self):
        result = rp.predictor([], lthr_rows(180), steady_block(), TODAY)
        self.assertTrue(result['available'])
        expected = rp.KM['half'] * 1000 / speed_at(180 * rp.HALF_HR_FRACTION)
        half = next(p for p in result['predictions'] if p['key'] == 'half')
        self.assertAlmostEqual(half['seconds'], expected, delta=2)
        self.assertEqual(result['model']['lthr'], 180)

    def test_garmin_is_only_a_comparison(self):
        garmin = [garmin_day(TODAY - timedelta(days=i), 4000) for i in range(30)]
        with_garmin = rp.predictor(garmin, lthr_rows(), steady_block(), TODAY)
        without = rp.predictor([], lthr_rows(), steady_block(), TODAY)
        self.assertEqual(with_garmin['predictions'], without['predictions'])
        self.assertEqual(with_garmin['comparisons'][0], {'key': 'garmin', 'label': 'Garmins prognos', 'half': 4000})

    def test_short_reps_and_races_do_not_move_the_model(self):
        base = rp.predictor([], lthr_rows(), steady_block(), TODAY)['predictions']
        noisy = steady_block() + [
            training_run(TODAY - timedelta(days=2), [178] * 6, minutes=3),
            training_run(TODAY - timedelta(days=5), [186] * 10, name='Varvet', race=True)]
        noisy[-2]['laps'] = [[180, 1500, 165, 8.0, 'ACTIVE']] * 6  # orimligt snabba korta drag
        noisy[-1]['laps'] = [[600, 1800, 186, 3.0, 'ACTIVE']] * 10  # varmt, långsamt lopp
        self.assertEqual(rp.predictor([], lthr_rows(), noisy, TODAY)['predictions'], base)

    def test_no_prediction_without_laps_near_threshold(self):
        easy_only = steady_block(hrs=(135, 140, 145, 150))
        result = rp.predictor([], lthr_rows(), easy_only, TODAY)
        self.assertFalse(result['available'])
        self.assertIn('nära tröskelpulsen', result['reason'])

    def test_race_is_shown_as_a_check_not_used(self):
        race = training_run(TODAY - timedelta(days=10), [180] * 10, name='X - Varvet (HM)', race=True)
        result = rp.predictor([], lthr_rows(), steady_block() + [race], TODAY)
        self.assertEqual(len(result['checks']), 1)
        self.assertIsNotNone(result['checks'][0]['predicted'])

    def test_history_has_gaps_where_the_model_lacked_data(self):
        early = [training_run(TODAY - timedelta(days=d), (140, 150, 160, 172, 174)) for d in range(150, 120, -3)]
        result = rp.predictor([], lthr_rows(days=200), early + steady_block(), TODAY, history_days=180)
        days_with = [h['t'] for h in result['history'] if 'half' in h]
        self.assertIn(TODAY.isoformat(), days_with)
        self.assertNotIn((TODAY - timedelta(days=60)).isoformat(), days_with)

    def test_target_gap_and_required_rate(self):
        target = {'distance': 'half', 'seconds': 4920, 'deadline': '2027-03-14'}
        result = rp.predictor([], lthr_rows(), steady_block(), TODAY, target=target)
        t = result['target']
        half = next(p for p in result['predictions'] if p['key'] == 'half')['seconds']
        self.assertEqual(t['gapSec'], half - 4920)
        self.assertAlmostEqual(t['neededPerWeekSec'], max(half - 4920, 0) / t['weeksLeft'], places=0)


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
