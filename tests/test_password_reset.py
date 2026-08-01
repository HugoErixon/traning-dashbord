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
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402


class PasswordResetTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.LOGIN_IP_LIMITER.clear()
        garmin_server.FORGOT_PASSWORD_LIMITER.clear()
        # Nollställ användarlagret och ge testanvändaren en verifierad e-post,
        # så att inloggning inte blockeras av email_not_verified.
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.USER_STORE._users['hugo']['email'] = 'hugo@example.com'
        garmin_server.USER_STORE._users['hugo']['email_verified'] = True
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()

    def request_reset(self, email='hugo@example.com'):
        with mock.patch.object(garmin_server, '_send_password_reset_email', return_value=True) as mocked:
            response = self.client.post('/api/forgot-password', json={'email': email})
        return response, mocked

    def test_forgot_password_is_public_and_generic_for_unknown_email(self):
        response, mocked = self.request_reset(email='nobody@example.com')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['ok'])
        mocked.assert_not_called()

    def test_forgot_password_sends_email_and_issues_token(self):
        response, mocked = self.request_reset()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['ok'])
        mocked.assert_called_once()
        username, token = mocked.call_args[0][1], mocked.call_args[0][2]
        self.assertEqual(username, 'hugo')
        self.assertTrue(token)

    def test_reset_password_with_valid_token_allows_login_with_new_password(self):
        self.request_reset()
        token = garmin_server.USER_STORE._users['hugo']['_reset_token']

        response = self.client.post('/api/reset-password', json={
            'token': token,
            'password': 'brand-new-password-1',
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['ok'])

        old_login = self.client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})
        new_login = self.client.post('/api/login', json={
            'username': 'hugo', 'password': 'brand-new-password-1',
        })

        self.assertEqual(old_login.status_code, 401)
        self.assertEqual(new_login.status_code, 200)

    def test_reset_password_token_is_single_use(self):
        self.request_reset()
        token = garmin_server.USER_STORE._users['hugo']['_reset_token']

        first = self.client.post('/api/reset-password', json={'token': token, 'password': 'first-new-password'})
        second = self.client.post('/api/reset-password', json={'token': token, 'password': 'second-new-password'})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.get_json()['code'], 'invalid_reset_token')

    def test_reset_password_rejects_invalid_token(self):
        response = self.client.post('/api/reset-password', json={
            'token': 'not-a-real-token', 'password': 'whatever-password',
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'invalid_reset_token')

    def test_reset_password_enforces_minimum_length(self):
        self.request_reset()
        token = garmin_server.USER_STORE._users['hugo']['_reset_token']

        response = self.client.post('/api/reset-password', json={'token': token, 'password': 'short'})

        self.assertEqual(response.status_code, 400)

    def test_forgot_password_is_rate_limited_per_ip(self):
        for _ in range(5):
            self.request_reset(email='nobody@example.com')
        limited = self.client.post('/api/forgot-password', json={'email': 'nobody@example.com'})

        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.get_json()['code'], 'too_many_requests')


if __name__ == '__main__':
    unittest.main()
