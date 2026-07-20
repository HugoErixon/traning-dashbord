import os
import unittest
from unittest import mock

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402


class SanitizeGeneratedSessionsTests(unittest.TestCase):
    def test_valid_sessions_pass_through_sorted(self):
        raw = [
            {'week': 31, 'dow': 2, 'type': 'lift', 'km': 0, 'title': 'Styrka', 'detail': 'Knäböj 5×5'},
            {'week': 30, 'dow': 4, 'type': 'easy', 'km': 8, 'title': 'Z2', 'detail': 'Lugnt'},
        ]
        result = garmin_server._sanitize_generated_sessions(raw, 30, 0, 40)
        self.assertEqual([(s['week'], s['dow']) for s in result], [(30, 4), (31, 2)])

    def test_filters_junk_and_out_of_range(self):
        raw = [
            'inte en dict',
            {'week': 'x', 'dow': 1, 'type': 'run', 'title': 'Trasig'},
            {'week': 29, 'dow': 5, 'type': 'run', 'title': 'Förfluten vecka'},
            {'week': 30, 'dow': 0, 'type': 'run', 'title': 'Före idag'},
            {'week': 45, 'dow': 1, 'type': 'run', 'title': 'Efter slutvecka'},
            {'week': 31, 'dow': 9, 'type': 'run', 'title': 'Ogiltig dag'},
            {'week': 31, 'dow': 1, 'type': 'yoga', 'title': 'Okänd typ'},
            {'week': 31, 'dow': 1, 'type': 'run', 'title': ''},
            {'week': 31, 'dow': 3, 'type': 'run', 'km': 10, 'title': 'Giltigt pass', 'detail': 'Intervaller'},
        ]
        result = garmin_server._sanitize_generated_sessions(raw, 30, 2, 40)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['title'], 'Giltigt pass')

    def test_one_session_per_day_first_wins(self):
        raw = [
            {'week': 31, 'dow': 1, 'type': 'run', 'title': 'Första'},
            {'week': 31, 'dow': 1, 'type': 'easy', 'title': 'Dubblett'},
        ]
        result = garmin_server._sanitize_generated_sessions(raw, 30, 0, 40)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['title'], 'Första')

    def test_km_clamped_and_defaulted(self):
        raw = [
            {'week': 31, 'dow': 1, 'type': 'easy', 'km': 999, 'title': 'För långt'},
            {'week': 31, 'dow': 2, 'type': 'lift', 'km': 'abc', 'title': 'Trasig km'},
        ]
        result = garmin_server._sanitize_generated_sessions(raw, 30, 0, 40)
        self.assertEqual(result[0]['km'], 60.0)
        self.assertEqual(result[1]['km'], 0.0)

    def test_current_week_respects_start_dow(self):
        raw = [
            {'week': 30, 'dow': 2, 'type': 'run', 'title': 'Idag'},
            {'week': 30, 'dow': 1, 'type': 'run', 'title': 'Igår'},
        ]
        result = garmin_server._sanitize_generated_sessions(raw, 30, 2, 40)
        self.assertEqual([s['title'] for s in result], ['Idag'])


class GeneratePlanEndpointTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server._TESTING_GOALS.clear()
        self.client = garmin_server.app.test_client()
        login = self.client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})
        self.csrf = login.get_json()['csrfToken']

    def test_requires_configured_llm(self):
        with mock.patch.object(garmin_server, 'LLM_PROVIDER', 'gemini'), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', ''):
            response = self.client.post('/api/plan/generate', headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 503)

    def test_requires_goal(self):
        with mock.patch.object(garmin_server, 'LLM_PROVIDER', 'gemini'), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'):
            response = self.client.post('/api/plan/generate', headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'goal_required')

    def test_rejects_invalid_ai_plan(self):
        garmin_server.save_user_goal(1, {'goal_title': 'Milen under 45', 'goal_deadline': None,
                                         'current_best': None, 'secondary_goal': None,
                                         'start_date': '2026-07-01'})
        with mock.patch.object(garmin_server, 'LLM_PROVIDER', 'gemini'), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'), \
             mock.patch.object(garmin_server, 'call_llm',
                               return_value='{"coaching_notes": "x", "sessions": []}'):
            response = self.client.post('/api/plan/generate', headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()['code'], 'ai_plan_invalid')


if __name__ == '__main__':
    unittest.main()
