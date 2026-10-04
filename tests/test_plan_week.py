import os
import unittest
from datetime import date
from unittest import mock

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402


# Onsdag i ISO-vecka 40.
WEDNESDAY = date(2026, 9, 30)


class FixedDate(date):
    @classmethod
    def today(cls):
        return WEDNESDAY


def session(dow, **kw):
    return {'dow': dow, 'type': 'run', 'km': 8, 'title': 'Intervaller',
            'detail': '6×1000 m', **kw}


class SanitizeWeekPlanTests(unittest.TestCase):
    def sanitize(self, week, sessions):
        return garmin_server._sanitize_week_plan(week, sessions, WEDNESDAY)

    def test_valid_week_is_sorted_and_clamped(self):
        result, error = self.sanitize(41, [
            session(5, type='easy', km=150, title='Långpass'),
            session(1, type='lift', km=None, title='Styrka'),
        ])
        self.assertIsNone(error)
        self.assertEqual([s['dow'] for s in result], [1, 5])
        self.assertEqual(result[0]['km'], 0)
        self.assertEqual(result[1]['km'], 100.0)
        self.assertTrue(all(s['week'] == 41 for s in result))

    def test_empty_week_is_allowed(self):
        self.assertEqual(self.sanitize(41, []), ([], None))

    def test_past_and_far_weeks_are_rejected(self):
        for week in (39, 40 + garmin_server.PLAN_WEEK_MAX_AHEAD + 1, 0):
            with self.subTest(week=week):
                self.assertIsNotNone(self.sanitize(week, [])[1])

    def test_stops_at_the_last_week_of_the_year(self):
        december = date(2026, 12, 2)  # vecka 49; 2026 har 53 ISO-veckor
        self.assertEqual(garmin_server._plannable_weeks(december), (49, 53))

    def test_passed_days_in_current_week_are_rejected(self):
        self.assertIsNotNone(self.sanitize(40, [session(1)])[1])
        self.assertIsNone(self.sanitize(40, [session(2)])[1])

    def test_rejects_bad_input(self):
        for bad in ([session(2), session(2)], [session(7)], [session(2, type='yoga')],
                    [session(2, title='  ')], [session(2, km='abc')], [session(2, km=-1)],
                    ['inte ett pass'], 'inte en lista'):
            with self.subTest(bad=bad):
                self.assertIsNotNone(self.sanitize(41, bad)[1])


class SaveWeekPlanEndpointTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        self.client = garmin_server.app.test_client()
        login = self.client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})
        self.csrf = login.get_json()['csrfToken']
        self.conn = mock.MagicMock()
        self.conn.__enter__.return_value = self.conn
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.cur.fetchall.return_value = []
        self.cur.rowcount = 2
        patches = [mock.patch.object(garmin_server, 'db', return_value=self.conn),
                   mock.patch.object(garmin_server, 'date', FixedDate)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def put(self, week, payload):
        return self.client.put(f'/api/plan/week/{week}', json=payload,
                               headers={'X-CSRF-Token': self.csrf})

    def statements(self, prefix):
        return [c.args for c in self.cur.execute.call_args_list
                if c.args[0].lstrip().startswith(prefix)]

    def test_replaces_only_planned_sessions_from_today(self):
        response = self.put(40, {'sessions': [session(2), session(4, type='lift', title='Styrka')]})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['sessions'], 2)
        (delete_sql, delete_params), = self.statements('DELETE')
        self.assertIn("status IN ('planned', 'rescheduled')", delete_sql)
        self.assertEqual(delete_params, (1, 40, 2))  # user, vecka, från onsdag
        inserts = self.statements('INSERT')
        self.assertEqual([p[1] for _, p in inserts], [2, 4])
        self.assertTrue(all(p[-1] == 1 for _, p in inserts))
        self.conn.commit.assert_called_once()

    def test_future_week_is_replaced_from_monday(self):
        self.assertEqual(self.put(42, {'sessions': []}).status_code, 200)
        (_, params), = self.statements('DELETE')
        self.assertEqual(params, (1, 42, 0))

    def test_day_with_finished_session_is_not_overwritten(self):
        self.cur.fetchall.return_value = [(2,)]
        response = self.put(40, {'sessions': [session(2)]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['dows'], [2])
        self.assertEqual(self.statements('DELETE'), [])
        self.conn.commit.assert_not_called()

    def test_invalid_plan_writes_nothing(self):
        response = self.put(40, {'sessions': [session(0)]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'invalid_week_plan')
        self.cur.execute.assert_not_called()

    def test_requires_csrf_token(self):
        response = self.client.put('/api/plan/week/41', json={'sessions': []})
        self.assertEqual(response.status_code, 403)


if __name__ == '__main__':
    unittest.main()
