import os
import unittest
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests@127.0.0.1:1/unused-in-tests'

import garmin_server  # noqa: E402
import plan_calendar  # noqa: E402
import season_plan  # noqa: E402
import vdot  # noqa: E402
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402


class FakeCursor:
    def __init__(self, log, results):
        self.log, self.results, self.rowcount = log, results, 0

    def execute(self, sql, params=None):
        self.log.append((' '.join(sql.split()), params))
        self.rowcount = 3 if sql.strip().startswith('DELETE') else 1

    def fetchall(self):
        sql = self.log[-1][0]
        for key, rows in self.results.items():
            if key in sql:
                return rows
        return []

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, log, results):
        self.log, self.results = log, results

    def cursor(self, *args, **kwargs):
        return FakeCursor(self.log, self.results)

    def commit(self):
        self.log.append(('COMMIT', None))


class TrainingApiTests(unittest.TestCase):
    TODAY = date(2026, 11, 28)  # lördag, planvecka 48

    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()
        self.sql = []
        self.results = {}
        zones = vdot.paces(55.2)
        frames = season_plan.build(date(2027, 3, 7), 'half', 54, 18, 48, zones=zones)
        meta = {'params': {'distance': 'half'}, 'raceName': 'Málaga halvmaraton',
                'target': {'distance': 'half', 'raceDate': date(2027, 3, 7), 'seconds': 4920}}
        pace = {'vdot': {'available': True, 'vdot': 55.2, 'source': 'predictor',
                         'sourceLabel': 'Prognos', 'paces': zones,
                         'goal': {'label': 'Halvmaraton 1:22:00', 'vdot': 57.0,
                                  'paces': vdot.paces(57.0)}}}

        @contextmanager
        def fake_db():
            yield FakeConn(self.sql, self.results)

        class FakeNow:
            @staticmethod
            def now(tz=None):
                from datetime import datetime
                return datetime(2026, 11, 28, 9, 0)

        self.patches = [
            patch.object(garmin_server, '_season_weeks', return_value=(frames, meta)),
            patch.object(garmin_server, '_pace_context', return_value=pace),
            patch.object(garmin_server, 'get_cache', return_value=None),
            patch.object(garmin_server, '_threshold_hr', return_value=None),
            patch.object(garmin_server, '_latest_lactate_hr', return_value=170),
            patch.object(garmin_server, '_season_target', return_value=meta['target']),
            patch.object(garmin_server, 'db', fake_db),
            patch.object(garmin_server, 'datetime', FakeNow),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def login(self):
        return self.client.post('/api/login', json={
            'username': 'hugo', 'password': 'test-password'}).get_json()['csrfToken']

    def test_routes_require_login(self):
        for url in ('/api/training-plan/preview', '/api/physiology', '/api/intensity',
                    '/api/training-library'):
            self.assertEqual(self.client.get(url).status_code, 401, url)

    def test_preview_starts_next_week_with_sessions_and_checks(self):
        self.login()
        data = self.client.get('/api/training-plan/preview?weeks=2').get_json()
        self.assertTrue(data['available'])
        self.assertEqual([w['week'] for w in data['weeks']], [49, 50])
        week = data['weeks'][0]
        self.assertTrue(week['sessions'])
        self.assertTrue(week['checks'])
        self.assertEqual(data['lthr'], {'value': 170, 'source': 'garmin'})
        first = week['sessions'][0]
        monday = plan_calendar.plan_monday(49)
        self.assertEqual(first['date'], date.fromordinal(monday.toordinal() + first['dow']).isoformat())

    def test_preview_current_week_skips_past_days(self):
        self.login()
        data = self.client.get('/api/training-plan/preview?weeks=1&from=current').get_json()
        self.assertEqual(data['weeks'][0]['week'], 48)
        self.assertTrue(all(s['dow'] >= 5 for s in data['weeks'][0]['sessions']))

    def test_preview_rejects_bad_options(self):
        self.login()
        self.assertEqual(self.client.get('/api/training-plan/preview?weeks=9').status_code, 400)
        self.assertEqual(self.client.get('/api/training-plan/preview?from=x').status_code, 400)

    def test_preview_without_season(self):
        self.login()
        with patch.object(garmin_server, '_season_weeks', return_value=(None, None)):
            data = self.client.get('/api/training-plan/preview').get_json()
        self.assertFalse(data['available'])
        self.assertIn('mållopp', data['reason'])

    def test_apply_requires_csrf(self):
        self.login()
        self.assertEqual(self.client.post('/api/training-plan/apply', json={'weeks': 1}).status_code, 403)

    def test_apply_replaces_only_planned_sessions_from_next_week(self):
        token = self.login()
        response = self.client.post('/api/training-plan/apply', json={'weeks': 1},
                                    headers={'X-CSRF-Token': token})
        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()
        self.assertEqual(body['weeks'], [49])
        self.assertGreater(body['applied'], 3)
        deletes = [(s, p) for s, p in self.sql if s.startswith('DELETE')]
        self.assertEqual(len(deletes), 1)
        self.assertIn("status='planned'", deletes[0][0])
        self.assertEqual(deletes[0][1][1:], (49, 0))
        inserts = [p for s, p in self.sql if s.startswith('INSERT INTO plan_sessions')]
        self.assertEqual(len(inserts), body['applied'])
        self.assertTrue(all(p[-2] == 'Trainyze planmotor' for p in inserts))
        self.assertTrue(all('Syfte:' in p[6] for p in inserts))

    def test_apply_keeps_completed_session_on_same_day_and_type(self):
        token = self.login()
        preview = self.client.get('/api/training-plan/preview?weeks=1').get_json()
        first = preview['weeks'][0]['sessions'][0]
        self.results["status='completed'"] = [(first['dow'], first['type'])]
        body = self.client.post('/api/training-plan/apply', json={'weeks': 1},
                                headers={'X-CSRF-Token': token}).get_json()
        inserts = [p for s, p in self.sql if s.startswith('INSERT INTO plan_sessions')]
        self.assertFalse(any(p[2] == first['dow'] and p[3] == first['type'] for p in inserts))
        self.assertEqual(body['applied'], len(inserts))

    def test_physiology_has_equivalents_zones_and_goal_gap(self):
        self.login()
        data = self.client.get('/api/physiology').get_json()
        self.assertTrue(data['available'])
        self.assertEqual(data['vdot']['vdot'], 55.2)
        self.assertIn('SUBT', data['zones'])
        self.assertGreater(data['zones']['SUBT']['lowSec'], data['zones']['T']['paceSec'])
        self.assertEqual(data['zones']['T']['hr'], {'low': 162, 'high': 170})
        labels = [e['label'] for e in data['equivalents']]
        self.assertEqual(len(labels), 6)
        goal = data['goal']
        self.assertAlmostEqual(goal['gap'], 1.8, places=1)
        self.assertIn(goal['realistic']['level'], ('realistic', 'ambitious', 'unrealistic'))
        self.assertEqual(goal['realistic']['level'], 'realistic')

    def test_intensity_sums_heart_rate_zones(self):
        self.login()
        self.results['FROM activities'] = [
            {'date': '2026-11-24', 'type': 'running', 'raw': {
                'activityType': {'typeKey': 'running'}, 'distance': 10000, 'duration': 3000,
                'hrTimeInZone_1': 600, 'hrTimeInZone_2': 1200, 'hrTimeInZone_3': 600,
                'hrTimeInZone_4': 480, 'hrTimeInZone_5': 120, 'activityTrainingLoad': 120}},
        ]
        data = self.client.get('/api/intensity?weeks=4').get_json()
        self.assertEqual(len(data['weeks']), 4)
        week = data['weeks'][-1]
        self.assertEqual(round(week['lowMin']), 40)
        self.assertEqual(round(week['thresholdMin']), 8)
        self.assertEqual(round(week['highMin']), 2)
        self.assertAlmostEqual(week['lowShare'], 0.8, places=2)
        self.assertIn('summary', data['load'])
        self.assertTrue(data['assumptions'])

    def test_training_library_has_groups_and_research(self):
        self.login()
        data = self.client.get('/api/training-library').get_json()
        self.assertTrue(data['available'])
        self.assertEqual(len(data['groups']), 7)
        self.assertTrue(all(g['sessions'] for g in data['groups']))
        self.assertTrue(data['research'])


if __name__ == '__main__':
    unittest.main()
