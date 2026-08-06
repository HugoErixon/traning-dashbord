import os
import unittest
from datetime import date
from unittest.mock import patch

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402
from adaptive_plan import AdaptivePlanStore  # noqa: E402
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402


class AdaptivePlanApiTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.ADAPTIVE_PLAN_STORE = AdaptivePlanStore()
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()

    def login(self):
        response = self.client.post('/api/login', json={
            'username': 'hugo', 'password': 'test-password',
        })
        return response.get_json()['csrfToken']

    @staticmethod
    def snapshot():
        return {
            'date': '2026-08-06',
            'session': {'id': 9, 'type': 'run', 'kind': 'threshold',
                        'is_quality': True, 'title': 'Tröskel', 'km': 10},
            'health': {'sleep_hours': 7.5, 'sleep_stale': False, 'readiness': 76},
            'checkin': {}, 'load': {'hard_days_last_3': 0},
        }

    def test_today_requires_login(self):
        self.assertEqual(self.client.get('/api/adaptive-plan/today').status_code, 401)

    def test_today_returns_a_shadow_decision(self):
        self.login()
        with patch.object(garmin_server, 'build_adaptive_snapshot', return_value=self.snapshot()):
            response = self.client.get('/api/adaptive-plan/today')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['mode'], 'shadow')
        self.assertEqual(payload['decision']['action'], 'keep')
        self.assertTrue(payload['decisionId'])

    def test_checkin_requires_csrf_and_recalculates(self):
        csrf = self.login()
        snapshot = self.snapshot()

        rejected = self.client.post('/api/adaptive-plan/checkin', json={'pain': 8})
        self.assertEqual(rejected.status_code, 403)

        def current_snapshot(_user_id):
            current = dict(snapshot)
            current['checkin'] = garmin_server.ADAPTIVE_PLAN_STORE.get_checkin(1, date.today())
            return current

        # The route owns today's date. Keep storage/evaluation deterministic in
        # this API test while still exercising the real check-in validation.
        with patch.object(garmin_server, 'build_adaptive_snapshot', side_effect=current_snapshot):
            response = self.client.post('/api/adaptive-plan/checkin', json={'pain': 8},
                                        headers={'X-CSRF-Token': csrf})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['decision']['action'], 'rest')

    def test_invalid_checkin_is_rejected(self):
        csrf = self.login()
        response = self.client.post('/api/adaptive-plan/checkin', json={'energy': 11},
                                    headers={'X-CSRF-Token': csrf})
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
