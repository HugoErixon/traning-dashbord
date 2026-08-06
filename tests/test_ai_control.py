import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402
from ai_control import AiControlStore  # noqa: E402


class AiControlSecurityTests(unittest.TestCase):
    def setUp(self):
        self.client = garmin_server.app.test_client()
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.AI_CONTROL_ENABLED = True
        garmin_server.AI_PASSKEY_BOOTSTRAP_TOKEN = 'bootstrap-' + ('x' * 40)
        garmin_server.AI_AGENT_TOKEN = 'agent-' + ('y' * 40)
        garmin_server.AI_CONTROL_STORE = AiControlStore()
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.LOGIN_IP_LIMITER.clear()

    def login(self):
        response = self.client.post('/api/login', json={
            'username': 'hugo', 'password': 'test-password',
        })
        return response.get_json()['csrfToken']

    def test_ai_page_and_api_are_not_public(self):
        responses = (self.client.get('/ai'), self.client.get('/ai.html'),
                     self.client.get('/api/ai/status'))
        self.assertEqual([response.status_code for response in responses], [403, 404, 401])
        for response in responses:
            response.close()

    def test_job_creation_requires_recent_passkey_step_up(self):
        csrf = self.login()
        response = self.client.post('/api/ai/jobs', json={'prompt': 'Fixa testet'},
                                    headers={'X-CSRF-Token': csrf})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'passkey_required')

    def test_unlocked_owner_can_queue_and_read_job(self):
        csrf = self.login()
        with self.client.session_transaction() as session:
            session['ai_verified_at'] = garmin_server.time.time()
        created = self.client.post('/api/ai/jobs', json={'prompt': 'Fixa testet'},
                                   headers={'X-CSRF-Token': csrf})
        self.assertEqual(created.status_code, 201)
        job_id = created.get_json()['job']['id']
        listed = self.client.get('/api/ai/jobs').get_json()['jobs']
        self.assertEqual([job['id'] for job in listed], [job_id])
        self.assertEqual(listed[0]['provider'], 'codex')

    def test_owner_can_select_claude_with_slash_command(self):
        csrf = self.login()
        with self.client.session_transaction() as session:
            session['ai_verified_at'] = garmin_server.time.time()
        created = self.client.post('/api/ai/jobs', json={'prompt': '/claude Fixa testet'},
                                   headers={'X-CSRF-Token': csrf})
        self.assertEqual(created.status_code, 201)
        job = created.get_json()['job']
        self.assertEqual(job['provider'], 'claude')
        self.assertEqual(job['prompt'], 'Fixa testet')

    def test_unknown_ai_slash_command_is_rejected(self):
        csrf = self.login()
        with self.client.session_transaction() as session:
            session['ai_verified_at'] = garmin_server.time.time()
        response = self.client.post('/api/ai/jobs', json={'prompt': '/root Fixa allt'},
                                    headers={'X-CSRF-Token': csrf})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'invalid_ai_provider')

    def test_agent_endpoints_use_separate_bearer_token(self):
        garmin_server.AI_CONTROL_STORE.create_job(1, 'Analysera felet')
        rejected = self.client.post('/api/ai/agent/jobs/next', json={'agentId': 'g3'})
        accepted = self.client.post('/api/ai/agent/jobs/next', json={'agentId': 'g3'},
                                    headers={'Authorization': f'Bearer {garmin_server.AI_AGENT_TOKEN}'})
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.get_json()['job']['status'], 'running')

    def test_first_passkey_needs_bootstrap_secret(self):
        csrf = self.login()
        rejected = self.client.post('/api/ai/passkeys/register/options',
                                    json={'bootstrapToken': 'wrong'},
                                    headers={'X-CSRF-Token': csrf})
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.get_json()['code'], 'invalid_bootstrap_token')

    def test_registration_options_require_face_id_capable_platform_authenticator(self):
        csrf = self.login()
        response = self.client.post(
            '/api/ai/passkeys/register/options',
            json={'bootstrapToken': garmin_server.AI_PASSKEY_BOOTSTRAP_TOKEN},
            headers={'X-CSRF-Token': csrf},
        )
        self.assertEqual(response.status_code, 200)
        options = response.get_json()
        self.assertEqual(options['rp']['id'], 'trainyze.com')
        self.assertEqual(options['authenticatorSelection']['authenticatorAttachment'], 'platform')
        self.assertEqual(options['authenticatorSelection']['residentKey'], 'required')
        self.assertEqual(options['authenticatorSelection']['userVerification'], 'required')

    def test_registration_requires_user_verification_and_unlocks_session(self):
        csrf = self.login()
        with self.client.session_transaction() as session:
            session['ai_registration_challenge'] = garmin_server._b64url(b'challenge-value')
            session['ai_registration_challenge_expires'] = garmin_server.time.time() + 60
            session['ai_registration_authorized'] = True
        verification = SimpleNamespace(
            credential_id=b'credential-id', credential_public_key=b'public-key', sign_count=0,
        )
        credential = {
            'id': 'credential', 'rawId': 'Y3JlZGVudGlhbC1pZA', 'type': 'public-key',
            'response': {'transports': ['internal']},
        }
        with mock.patch.object(garmin_server, 'verify_registration_response',
                               return_value=verification) as verify:
            response = self.client.post('/api/ai/passkeys/register/verify',
                                        data=json.dumps({'credential': credential}),
                                        content_type='application/json',
                                        headers={'X-CSRF-Token': csrf})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(verify.call_args.kwargs['require_user_verification'])
        self.assertEqual(len(garmin_server.AI_CONTROL_STORE.credentials_for_user(1)), 1)
        with self.client.session_transaction() as session:
            self.assertGreater(session['ai_verified_at'], 0)


if __name__ == '__main__':
    unittest.main()
