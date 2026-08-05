"""Sömnanalysen ska vara data, inte ett färdigt HTTP-svar.

_get_sleep_insights har två läsare: sömnsidans endpoint, som vill ha ett svar
att returnera, och chatten, som bygger in analysen i sin prompt med json.dumps.
När cacheträffen returnerade en Response gick den andra läsaren sönder — och
bara den, vilket gjorde felet svårt att se: första sömnfrågan värmde cachen och
svarade normalt, varje följdfråga kraschade med 502.
"""
import json
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
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402


CACHED_INSIGHTS = {
    'status': 'watch',
    'headline': 'Sen läggtid drar ner djupsömnen',
    'insights': [{'title': 'Läggtid', 'detail': 'Snitt 00:40.', 'action': 'Sikte på 23:30.'}],
}


class SleepInsightsCacheTests(unittest.TestCase):
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

    def warm_cache(self):
        return mock.patch.object(garmin_server, 'get_cache',
                                 return_value=(CACHED_INSIGHTS, time.time()))

    def test_a_cache_hit_returns_data_the_chat_prompt_can_serialise(self):
        self.login()
        with self.client.application.test_request_context():
            with self.warm_cache(), mock.patch.object(garmin_server, 'uid', return_value=1):
                insights = garmin_server._get_sleep_insights()

        # Det som gick sönder: json.dumps på en Response kastar TypeError.
        self.assertEqual(json.loads(json.dumps(insights, ensure_ascii=False)),
                         CACHED_INSIGHTS)

    def test_the_sleep_page_still_gets_the_cached_analysis_as_json(self):
        self.login()
        with self.warm_cache():
            response = self.client.get('/api/sleep/insights')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), CACHED_INSIGHTS)

    def test_a_sleep_follow_up_is_answered_when_the_cache_is_warm(self):
        csrf = self.login().get_json()['csrfToken']
        with self.warm_cache(), \
             mock.patch.object(garmin_server, 'llm_available', return_value=True), \
             mock.patch.object(garmin_server, '_is_plan_change_request', return_value=False), \
             mock.patch.object(garmin_server, '_build_sleep_coach', return_value={'bedtime': '23:30'}), \
             mock.patch.object(garmin_server, '_recent_execution_block', return_value=''), \
             mock.patch.object(garmin_server, '_pace_context', return_value={}), \
             mock.patch.object(garmin_server, 'call_llm', return_value='Luftfuktighet spelar roll.') as llm:
            response = self.client.post('/api/assistant', json={
                'message': 'Hur såg min sömn ut i natt?',
                'context': 'Du är coach.',
            }, headers={'X-CSRF-Token': csrf})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['reply'], 'Luftfuktighet spelar roll.')
        # Analysen ska faktiskt ha nått prompten, inte bara ha undvikit kraschen.
        self.assertIn(CACHED_INSIGHTS['headline'], llm.call_args.kwargs['system'])


if __name__ == '__main__':
    unittest.main()
