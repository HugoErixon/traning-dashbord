"""API:erna för labbtest, känningar och coachens instruktion."""
import os
import time
import unittest
from unittest import mock

from werkzeug.security import generate_password_hash

os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402


class MemoryCache:
    def __init__(self):
        self.store = {}

    def get(self, key, user_id=1):
        return self.store.get((user_id, key))

    def set(self, key, value, user_id=1):
        self.store[(user_id, key)] = (value, time.time())


class CoachApiTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        self.cache = MemoryCache()
        self.client = garmin_server.app.test_client()
        login = self.client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})
        self.csrf = login.get_json()['csrfToken']
        patches = [
            mock.patch.object(garmin_server, 'get_cache', self.cache.get),
            mock.patch.object(garmin_server, 'set_cache', self.cache.set),
            mock.patch.object(garmin_server, '_store_race_prediction', return_value={}),
            mock.patch.object(garmin_server, '_pace_context', return_value={'vdot': {'available': False}}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def post(self, url, body):
        return self.client.post(url, json=body, headers={'X-CSRF-Token': self.csrf})

    def test_lab_test_sets_threshold_hr_from_the_lab(self):
        res = self.post('/api/lab-tests', {'date': '2026-11-01', 'lt1Hr': 158, 'lt2Hr': 180,
                                           'lt2Pace': '3:58', 'maxHr': 196})
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(self.cache.get('threshold-hr:v1')[0], {'hr': 180, 'source': 'lab', 'date': '2026-11-01'})
        self.assertEqual(garmin_server._manual_lthr_source(1), 'lab')
        self.assertIn('LAB TEST 2026-11-01', garmin_server._lab_prompt_block(1))
        bad = self.post('/api/lab-tests', {'date': '2026-11-01', 'lt1Hr': 190, 'lt2Hr': 180})
        self.assertEqual(bad.status_code, 400)
        gone = self.client.delete('/api/lab-tests/2026-11-01', headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(gone.status_code, 200)
        self.assertEqual(self.cache.get('threshold-hr:v1')[0], {'hr': None})

    def test_niggle_log_and_prompt(self):
        res = self.post('/api/niggles', {'area': 'foot', 'side': 'left', 'severity': 4, 'note': 'trampdyna'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()['active'][0]['label'], 'Fot / trampdyna (vänster)')
        self.assertIn('ACTIVE NIGGLES', garmin_server._niggles_prompt_block(1))
        self.assertEqual(self.post('/api/niggles', {'area': 'elbow', 'severity': 2}).status_code, 400)

    def test_coach_prompt_has_no_sentence_cap_and_ignores_client_context(self):
        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['gemini']), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'), \
             mock.patch.object(garmin_server, 'call_llm', return_value='Svar') as llm:
            res = self.post('/api/assistant', {'message': 'Hur ska jag lägga veckan?',
                                               'context': 'Max 3-4 meningar.'})
        self.assertEqual(res.status_code, 200)
        system = llm.call_args.kwargs['system']
        self.assertIn('Trainyzes personliga coach för löpning och styrka', system)
        self.assertNotIn('Max 3-4 meningar', system)
        self.assertEqual(llm.call_args.kwargs['provider_overrides']['claude_cli']['model'],
                         garmin_server.CLAUDE_CLI_CHAT_MODEL)


if __name__ == '__main__':
    unittest.main()


class ProviderOverrideTests(unittest.TestCase):
    def test_override_changes_model_and_timeout_for_that_provider_only(self):
        seen = []

        def fake(prompt, max_tokens, system, timeout, spec, allow_wait, **kw):
            seen.append((spec['kind'], spec['model'], timeout))
            return 'ok'
        spec = {'kind': 'claude_cli', 'model': 'sonnet', 'label': 'Claude', 'key': 'claude'}
        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['claude_cli']), \
             mock.patch.object(garmin_server, '_provider_configured', return_value=True), \
             mock.patch.object(garmin_server, '_provider_spec', return_value=spec), \
             mock.patch.dict(garmin_server._LLM_CALLERS, {'claude_cli': fake}):
            garmin_server.call_llm('hej', timeout=40,
                                   provider_overrides={'claude_cli': {'model': 'opus', 'timeout': 90}})
            garmin_server.call_llm('hej', timeout=40)
        self.assertEqual(seen, [('claude_cli', 'opus', 90), ('claude_cli', 'sonnet', 40)])
