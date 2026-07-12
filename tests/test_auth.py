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


class AuthContractTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        self.client = garmin_server.app.test_client()

    def login(self):
        return self.client.post('/api/login', json={
            'username': 'hugo',
            'password': 'test-password',
        })

    def test_health_check_is_public_but_api_status_requires_auth(self):
        health = self.client.get('/api/healthz')
        protected = self.client.get('/api/status')

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json(), {'status': 'ok'})
        self.assertEqual(protected.status_code, 401)
        self.assertEqual(protected.get_json()['code'], 'authentication_required')

    def test_localhost_no_longer_bypasses_auth(self):
        response = self.client.get('/api/status', base_url='http://localhost')

        self.assertEqual(response.status_code, 401)

    def test_login_sets_http_only_strict_session_cookie(self):
        response = self.login()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['csrfToken'])
        cookie = response.headers['Set-Cookie']
        self.assertIn('training_session=', cookie)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)

    def test_session_endpoint_returns_csrf_token(self):
        self.login()

        response = self.client.get('/api/session')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['authenticated'])
        self.assertTrue(response.get_json()['csrfToken'])

    def test_mutating_api_requires_csrf_token(self):
        login = self.login().get_json()

        rejected = self.client.post('/api/logout')
        accepted = self.client.post('/api/logout', headers={
            'X-CSRF-Token': login['csrfToken'],
        })

        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.get_json()['code'], 'invalid_csrf_token')
        self.assertEqual(accepted.status_code, 200)
        self.assertFalse(self.client.get('/api/session').get_json()['authenticated'])

    def test_invalid_login_response_is_generic(self):
        response = self.client.post('/api/login', json={
            'username': 'missing-user',
            'password': 'wrong',
        })

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()['code'], 'invalid_credentials')
        self.assertNotIn('missing-user', response.get_data(as_text=True))

    def test_unhandled_errors_do_not_leak_exception_text(self):
        self.login()
        with mock.patch.object(
            garmin_server,
            '_mobile_widget_payload',
            side_effect=RuntimeError('database-password-secret'),
        ):
            response = self.client.get('/api/widget/mobile')

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()['code'], 'internal_error')
        self.assertNotIn('database-password-secret', response.get_data(as_text=True))
        self.assertTrue(response.headers['X-Request-ID'])

    def test_security_and_cache_headers_are_present(self):
        response = self.client.get('/')
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
        self.assertNotIn("script-src 'self' 'unsafe-inline'", response.headers['Content-Security-Policy'])
        response.close()

        api_response = self.client.get('/api/session')
        self.assertEqual(api_response.headers['Cache-Control'], 'no-store')
        api_response.close()


if __name__ == '__main__':
    unittest.main()
