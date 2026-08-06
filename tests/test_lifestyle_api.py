import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402
from lifestyle import LifestyleStore  # noqa: E402
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402


class LifestyleApiTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.LIFESTYLE_STORE = LifestyleStore()
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()

    def login(self):
        result = self.client.post('/api/login', json={
            'username': 'hugo', 'password': 'test-password'}).get_json()
        return result['csrfToken']

    def test_log_requires_authentication(self):
        self.assertEqual(self.client.get('/api/lifestyle').status_code, 401)

    def test_save_requires_csrf_and_round_trips(self):
        csrf = self.login()
        target = (date.today() - timedelta(days=1)).isoformat()
        rejected = self.client.post('/api/lifestyle', json={'date': target, 'alcohol_drinks': 2})
        self.assertEqual(rejected.status_code, 403)
        with patch.object(garmin_server, '_lifestyle_insights', return_value={'insights': []}):
            saved = self.client.post('/api/lifestyle', json={
                'date': target, 'alcohol_drinks': 2, 'water_liters': 2.3,
            }, headers={'X-CSRF-Token': csrf})
            loaded = self.client.get(f'/api/lifestyle?date={target}')
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(loaded.get_json()['entry']['alcohol_drinks'], 2)

    def test_future_and_invalid_values_are_rejected(self):
        csrf = self.login()
        future = (date.today() + timedelta(days=1)).isoformat()
        self.assertEqual(self.client.post('/api/lifestyle', json={'date': future},
                                         headers={'X-CSRF-Token': csrf}).status_code, 400)
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.assertEqual(self.client.post('/api/lifestyle', json={
            'date': yesterday, 'water_liters': 99,
        }, headers={'X-CSRF-Token': csrf}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
