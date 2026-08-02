import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402


class StravaConnectFlowTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server._TESTING_STRAVA_STATES.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patchers = [
            mock.patch.object(garmin_server, 'STRAVA_CLIENT_ID', '123'),
            mock.patch.object(garmin_server, 'STRAVA_CLIENT_SECRET', 'server-secret'),
            mock.patch.object(garmin_server, 'STRAVA_REDIRECT_URI',
                              'https://trainyze.test/strava/callback'),
            mock.patch.object(garmin_server, 'STRAVA_TOKEN_ROOT', Path(self.tmp.name)),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = garmin_server.app.test_client()
        login = self.client.post('/api/login', json={
            'username': 'hugo', 'password': 'test-password',
        })
        self.csrf = login.get_json()['csrfToken']

    def post(self, path):
        return self.client.post(path, headers={'X-CSRF-Token': self.csrf})

    def authorization_state(self):
        response = self.post('/api/strava/connect')
        self.assertEqual(response.status_code, 200)
        url = response.get_json()['authorizationUrl']
        return parse_qs(urlparse(url).query)['state'][0]

    def test_connect_and_callback_store_server_side_token(self):
        state = self.authorization_state()
        token = {
            'access_token': 'access', 'refresh_token': 'refresh',
            'expires_at': 9999999999,
            'athlete': {'id': 8, 'firstname': 'Ada', 'lastname': 'Runner'},
        }
        with mock.patch.object(garmin_server.strava_integration, 'exchange_code',
                               return_value=token):
            callback = self.client.get('/strava/callback', query_string={
                'state': state, 'code': 'oauth-code',
                'scope': 'read,activity:read_all',
            })
        self.assertEqual(callback.status_code, 200)
        self.assertIn(b'data-strava-status="connected"', callback.data)
        saved = garmin_server._read_strava_tokens('hugo')
        self.assertEqual(saved['refresh_token'], 'refresh')
        self.assertEqual(saved['athlete']['id'], 8)

        status = self.client.get('/api/strava/status').get_json()
        self.assertTrue(status['connected'])
        self.assertEqual(status['athleteName'], 'Ada Runner')

        replay = self.client.get('/strava/callback', query_string={
            'state': state, 'code': 'oauth-code', 'scope': 'read,activity:read_all',
        })
        self.assertEqual(replay.status_code, 400)
        self.assertIn(b'data-strava-status="expired"', replay.data)

    def test_callback_rejects_missing_private_activity_scope(self):
        state = self.authorization_state()
        with mock.patch.object(garmin_server.strava_integration, 'exchange_code',
                               return_value={'access_token': 'a', 'refresh_token': 'r'}):
            response = self.client.get('/strava/callback', query_string={
                'state': state, 'code': 'code', 'scope': 'read',
            })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(garmin_server._strava_token_path('hugo').exists())

    def test_detail_rejects_activity_owned_by_another_athlete(self):
        garmin_server._save_strava_tokens('hugo', {
            'access_token': 'a', 'refresh_token': 'r', 'expires_at': 9999999999,
            'athlete': {'id': 8},
        })
        with mock.patch.object(garmin_server.strava_integration, 'activity_detail',
                               return_value={'id': 55, '_athleteId': 99}):
            response = self.client.get('/api/strava/activities/55')
        self.assertEqual(response.status_code, 404)

    def test_disconnect_deletes_local_token_even_if_revoke_fails(self):
        garmin_server._save_strava_tokens('hugo', {
            'access_token': 'a', 'refresh_token': 'r', 'expires_at': 9999999999,
        })
        with mock.patch.object(garmin_server.requests, 'post',
                               side_effect=garmin_server.requests.RequestException):
            response = self.post('/api/strava/disconnect')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(garmin_server._strava_token_path('hugo').exists())


if __name__ == '__main__':
    unittest.main()
