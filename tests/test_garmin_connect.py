import os
import tempfile
import unittest
from pathlib import Path
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


class FakeGarthClient:
    def dump(self, path):
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        (p / 'garmin_tokens.json').write_text('{"fake": true}')


class FakeGarmin:
    mfa_required = True
    fail_login = False
    valid_code = '123456'

    def __init__(self, email=None, password=None, is_cn=False,
                 prompt_mfa=None, return_on_mfa=False):
        self.email = email
        self.password = password
        self.return_on_mfa = return_on_mfa
        self.client = FakeGarthClient()

    def login(self, tokenstore=None):
        if FakeGarmin.fail_login:
            raise RuntimeError('bad credentials')
        if FakeGarmin.mfa_required and self.return_on_mfa:
            return ('needs_mfa', None)
        return (None, None)

    def resume_login(self, client_state, mfa_code):
        if mfa_code != FakeGarmin.valid_code:
            raise RuntimeError('wrong code')
        return (None, None)


class GarminConnectFlowTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.GARMIN_CONNECT_LIMITER.clear()
        garmin_server._pending_garmin_mfa.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()

        FakeGarmin.mfa_required = True
        FakeGarmin.fail_login = False

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        token_root = Path(self.tmp.name)
        patchers = [
            mock.patch.object(garmin_server, 'Garmin', FakeGarmin),
            mock.patch.object(garmin_server, '_garmin_token_dir',
                              lambda username: token_root / username),
            mock.patch.object(garmin_server, 'TOKEN_DIR', str(token_root / '_legacy')),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

        self.client = garmin_server.app.test_client()
        # Kopplingstesterna körs som "frida" (icke-första användare, ingen legacy-fallback).
        admin_csrf = self.login().get_json()['csrfToken']
        self.client.post('/api/users', json={
            'username': 'frida',
            'password': 'super-secret-1',
        }, headers={'X-CSRF-Token': admin_csrf})
        self.client.post('/api/logout', headers={'X-CSRF-Token': admin_csrf})
        self.csrf = self.login('frida', 'super-secret-1').get_json()['csrfToken']

    def login(self, username='hugo', password='test-password'):
        return self.client.post('/api/login', json={
            'username': username,
            'password': password,
        })

    def connect(self, email='frida@example.com', password='garmin-pw'):
        return self.client.post('/api/garmin/connect', json={
            'email': email,
            'password': password,
        }, headers={'X-CSRF-Token': self.csrf})

    def mfa(self, state_id, code):
        return self.client.post('/api/garmin/mfa', json={
            'stateId': state_id,
            'code': code,
        }, headers={'X-CSRF-Token': self.csrf})

    def test_connect_requires_auth(self):
        anonymous = garmin_server.app.test_client()
        response = anonymous.post('/api/garmin/connect', json={
            'email': 'a@b.se', 'password': 'x',
        })
        self.assertEqual(response.status_code, 401)

    def test_connect_without_mfa(self):
        FakeGarmin.mfa_required = False

        response = self.connect()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()['mfaRequired'])
        self.assertTrue((Path(self.tmp.name) / 'frida' / 'garmin_tokens.json').is_file())
        self.assertTrue(self.client.get('/api/session').get_json()['garminConnected'])

    def test_connect_with_mfa_flow(self):
        started = self.connect()
        self.assertEqual(started.status_code, 200)
        self.assertTrue(started.get_json()['mfaRequired'])
        state_id = started.get_json()['stateId']

        verified = self.mfa(state_id, '123456')

        self.assertEqual(verified.status_code, 200)
        self.assertTrue(verified.get_json()['connected'])
        self.assertTrue((Path(self.tmp.name) / 'frida' / 'garmin_tokens.json').is_file())

    def test_wrong_mfa_code_consumes_state(self):
        state_id = self.connect().get_json()['stateId']

        wrong = self.mfa(state_id, '000000')
        retry = self.mfa(state_id, '123456')

        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(wrong.get_json()['code'], 'invalid_mfa_code')
        self.assertEqual(retry.status_code, 410)
        self.assertFalse(self.client.get('/api/session').get_json()['garminConnected'])

    def test_unknown_mfa_state_rejected(self):
        response = self.mfa('no-such-state', '123456')
        self.assertEqual(response.status_code, 410)

    def test_failed_garmin_login_is_generic_and_rate_limited(self):
        FakeGarmin.fail_login = True

        first = self.connect(email='hemlig@example.com')
        self.assertEqual(first.status_code, 400)
        self.assertEqual(first.get_json()['code'], 'garmin_login_failed')
        self.assertNotIn('hemlig', first.get_data(as_text=True))

        for _ in range(4):
            self.connect()
        limited = self.connect()
        self.assertEqual(limited.status_code, 429)

    def test_credential_validation(self):
        missing_at = self.connect(email='inte-en-mejl')
        empty_password = self.connect(password='')

        self.assertEqual(missing_at.status_code, 400)
        self.assertEqual(empty_password.status_code, 400)

    def test_disconnect_removes_tokens(self):
        FakeGarmin.mfa_required = False
        self.connect()

        response = self.client.post('/api/garmin/disconnect',
                                    headers={'X-CSRF-Token': self.csrf})

        self.assertEqual(response.status_code, 200)
        self.assertFalse((Path(self.tmp.name) / 'frida').exists())
        self.assertFalse(self.client.get('/api/session').get_json()['garminConnected'])


if __name__ == '__main__':
    unittest.main()
