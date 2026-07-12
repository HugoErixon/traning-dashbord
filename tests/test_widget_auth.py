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


class WidgetAuthTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()

    def login(self):
        return self.client.post('/api/login', json={
            'username': 'hugo',
            'password': 'test-password',
        })

    def issue_token(self):
        csrf = self.login().get_json()['csrfToken']
        response = self.client.post('/api/widget/token', headers={'X-CSRF-Token': csrf})
        self.assertEqual(response.status_code, 200)
        return response.get_json()['token'], csrf

    def widget_request(self, token):
        with mock.patch.object(
            garmin_server,
            '_mobile_widget_payload',
            side_effect=lambda user_id: {'userId': user_id},
        ):
            return self.client.get('/api/widget/mobile', headers={
                'Authorization': f'Bearer {token}',
            })

    def test_widget_endpoint_requires_session_or_widget_token(self):
        response = self.client.get('/api/widget/mobile')

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()['code'], 'authentication_required')

    def test_token_creation_requires_session_csrf(self):
        self.login()

        response = self.client.post('/api/widget/token')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'invalid_csrf_token')

    def test_bearer_token_authenticates_correct_user(self):
        token, _ = self.issue_token()

        response = self.widget_request(token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'userId': 1})

    def test_invalid_and_legacy_credentials_are_rejected(self):
        invalid = self.widget_request('tdw_not-valid')
        legacy = self.client.get('/api/widget/mobile', headers={
            'x-site-user': 'hugo',
            'x-site-password': 'test-password',
        })

        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(invalid.get_json()['code'], 'invalid_widget_token')
        self.assertEqual(legacy.status_code, 401)

    def test_rotating_token_invalidates_previous_token(self):
        first, csrf = self.issue_token()
        second_response = self.client.post('/api/widget/token', headers={'X-CSRF-Token': csrf})
        second = second_response.get_json()['token']

        self.assertNotEqual(first, second)
        self.assertEqual(self.widget_request(first).status_code, 401)
        self.assertEqual(self.widget_request(second).status_code, 200)

    def test_revoking_token_invalidates_it(self):
        token, csrf = self.issue_token()

        revoked = self.client.delete('/api/widget/token', headers={'X-CSRF-Token': csrf})

        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(self.widget_request(token).status_code, 401)


if __name__ == '__main__':
    unittest.main()
