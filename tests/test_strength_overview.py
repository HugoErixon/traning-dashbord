import os
import unittest
from datetime import date

from werkzeug.security import generate_password_hash

os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402
import strength_overview as so  # noqa: E402
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402

TODAY = date(2026, 11, 9)


def row(rid, day, exercise, sets, reps, weight, created=None):
    return {'id': rid, 'session_id': day, 'exercise': exercise, 'sets': sets, 'reps': reps,
            'weight': weight, 'note': '', 'created_at': created or rid}


class OverviewTests(unittest.TestCase):
    def entries(self, rows, dates=None):
        return so.normalize_rows(rows, dates or {}, TODAY)

    def test_progress_is_estimated_max_from_the_best_set_of_each_session(self):
        data = so.overview(self.entries([
            row(1, '2026-10-01', 'Bänkpress', 3, '8', 60),
            row(2, '2026-10-15', 'Bänkpress', 2, '8', 62.5),
            row(3, '2026-10-15', 'Bänkpress', 1, '6', 62.5),
            row(4, '2026-11-02', 'bänkpress ', 3, '8', 65),
        ]), TODAY, 90)
        ex = data['exercises'][0]
        self.assertEqual(ex['exercise'], 'bänkpress')
        self.assertEqual([p['t'] for p in ex['series']], ['2026-10-01', '2026-10-15', '2026-11-02'])
        self.assertEqual(ex['current'], so.e1rm(65, 8))
        self.assertEqual(ex['change'], round(so.e1rm(65, 8) - so.e1rm(60, 8), 1))
        self.assertEqual(ex['series'][1]['label'], '2×8 @ 62.5 kg, 1×6 @ 62.5 kg')
        # Två pass slog allt före dem; det första passet är ingen "ny" rekord.
        self.assertEqual(len(data['records']), 2)

    def test_window_and_previous_period(self):
        data = so.overview(self.entries([
            row(1, '2026-07-01', 'Rodd', 3, '10', 50),
            row(2, '2026-10-20', 'Rodd', 3, '10', 55),
        ]), TODAY, 30)
        self.assertEqual(data['summary']['sessions'], 1)
        self.assertEqual(data['summary']['volume'], 1650)
        self.assertEqual(data['summary']['previousSessions'], 0)
        self.assertEqual(data['last']['rodd']['label'], '3×10 @ 55 kg')
        # Varje vecka i perioden finns med, även tomma.
        self.assertGreaterEqual(len(data['weeks']), 5)
        self.assertEqual(sum(w['sessions'] for w in data['weeks']), 1)

    def test_bodyweight_sets_count_but_give_no_volume(self):
        data = so.overview(self.entries([row(1, '2026-11-01', 'Chins', 3, '8', None)]), TODAY, 30)
        self.assertEqual(data['summary']['sets'], 3)
        self.assertEqual(data['summary']['volume'], 0)
        self.assertFalse(data['exercises'][0]['weighted'])

    def test_activity_id_sessions_use_the_garmin_date(self):
        data = so.overview(self.entries([row(1, '123', 'Marklyft', 3, '5', 100)],
                                        {'123': '2026-11-05'}), TODAY, 30)
        self.assertEqual(data['sessions'][0]['date'], '2026-11-05')

    def test_last_performance_ignores_today(self):
        entries = self.entries([row(1, '2026-11-01', 'Knäböj', 3, '5', 80),
                                row(2, TODAY.isoformat(), 'Knäböj', 1, '5', 85)])
        self.assertEqual(so.last_performance(entries, before=TODAY.isoformat())['knäböj']['weight'], 80)


class MergeTests(unittest.TestCase):
    rows = [{'id': 1, 'exercise': 'Bänkpress', 'reps': '8', 'weight': 60, 'created_at': 10},
            {'id': 2, 'exercise': 'Rodd', 'reps': '10', 'weight': 50, 'created_at': 20}]

    def test_same_set_right_after_merges(self):
        self.assertEqual(so.merge_target(self.rows, 'rodd', '10', 50.0)['id'], 2)

    def test_different_reps_or_weight_starts_a_new_row(self):
        self.assertIsNone(so.merge_target(self.rows, 'Rodd', '9', 50))
        self.assertIsNone(so.merge_target(self.rows, 'Rodd', '10', 52.5))

    def test_only_the_latest_row_can_merge(self):
        # Bänk efter rodd blir en ny rad, annars hamnar setet före rodden.
        self.assertIsNone(so.merge_target(self.rows, 'Bänkpress', '8', 60))

    def test_bodyweight_merges_with_bodyweight(self):
        rows = [{'id': 3, 'exercise': 'Chins', 'reps': '8', 'weight': None, 'created_at': 1}]
        self.assertEqual(so.merge_target(rows, 'Chins', '8', None)['id'], 3)


class EndpointValidationTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()
        token = self.client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})
        self.headers = {'X-CSRF-Token': (token.get_json() or {}).get('csrfToken', '')}

    def test_requires_a_session(self):
        self.assertEqual(garmin_server.app.test_client().get('/api/strength/overview').status_code, 401)

    def test_rejects_bad_sets_before_touching_the_database(self):
        for body in ({'exercise': '', 'reps': 8}, {'exercise': 'Bänk', 'reps': 0},
                     {'exercise': 'Bänk', 'reps': 8, 'weight': 'tung'},
                     {'exercise': 'Bänk', 'reps': 8, 'weight': 2000}):
            response = self.client.post('/api/strength/2026-11-09/sets', json=body, headers=self.headers)
            self.assertEqual(response.status_code, 400, body)

    def test_rejects_unknown_session_ids(self):
        response = self.client.post('/api/strength/drop table/sets',
                                    json={'exercise': 'Bänk', 'reps': 8}, headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_patch_only_accepts_known_fields(self):
        response = self.client.patch('/api/strength/exercises/1', json={'note; drop': 1}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        response = self.client.patch('/api/strength/exercises/1', json={'sets': 0}, headers=self.headers)
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
