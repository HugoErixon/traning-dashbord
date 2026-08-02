"""Beteendet när AI-leverantören är slut på kvot.

Gratisnivån hos Gemini tar slut i toppar, och då ska dashboarden degradera
mjukt i stället för att visa fel: en utgången analys är fortfarande relevant
för dagens pass. Testerna låser fast den skillnaden.
"""
import os
import threading
import time
import unittest
from unittest.mock import patch

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402


REVIEW = {'status': 'done', 'headline': 'Bra pass', 'body': 'Du körde enligt plan.',
          '_review_version': 2}


class TrainingReviewQuotaTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()
        self.client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})

    def test_fresh_cache_is_served_without_calling_the_provider(self):
        with patch.object(garmin_server, 'llm_available', return_value=True), \
             patch.object(garmin_server, 'get_cache', return_value=(REVIEW, time.time())), \
             patch.object(garmin_server, 'call_llm') as llm:
            response = self.client.get('/api/training-review')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['headline'], 'Bra pass')
        llm.assert_not_called()

    def test_expired_cache_is_served_when_the_quota_is_exhausted(self):
        # 90 minuter gammal: för gammal för att serveras rakt av, men fullt
        # användbar när alternativet är ett felmeddelande.
        stale_at = time.time() - 90 * 60
        with patch.object(garmin_server, 'llm_available', return_value=True), \
             patch.object(garmin_server, 'get_cache', return_value=(REVIEW, stale_at)), \
             patch.object(garmin_server, '_build_review_prompt', return_value='p'), \
             patch.object(garmin_server, 'call_llm',
                          side_effect=garmin_server.LLMQuotaError('429', retry_after=51.9)):
            response = self.client.get('/api/training-review')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['headline'], 'Bra pass')
        self.assertTrue(payload['_stale'])
        self.assertEqual(payload['_stale_age_min'], 90)

    def test_expired_cache_is_served_for_any_provider_failure(self):
        # Inte bara kvotfel — ett trasigt svar ska också falla tillbaka.
        stale_at = time.time() - 45 * 60
        with patch.object(garmin_server, 'llm_available', return_value=True), \
             patch.object(garmin_server, 'get_cache', return_value=(REVIEW, stale_at)), \
             patch.object(garmin_server, '_build_review_prompt', return_value='p'), \
             patch.object(garmin_server, 'call_llm', side_effect=RuntimeError('tomt svar')):
            response = self.client.get('/api/training-review')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['_stale'])

    def test_without_any_cache_the_failure_still_surfaces(self):
        # Ingen cache att falla tillbaka på: då ska felet synas, inte döljas.
        with patch.object(garmin_server, 'llm_available', return_value=True), \
             patch.object(garmin_server, 'get_cache', return_value=None), \
             patch.object(garmin_server, '_build_review_prompt', return_value='p'), \
             patch.object(garmin_server, 'call_llm',
                          side_effect=garmin_server.LLMQuotaError('429')):
            response = self.client.get('/api/training-review')

        self.assertEqual(response.status_code, 500)

    def test_force_refresh_still_falls_back_rather_than_erroring(self):
        with patch.object(garmin_server, 'llm_available', return_value=True), \
             patch.object(garmin_server, 'get_cache', return_value=(REVIEW, time.time())), \
             patch.object(garmin_server, '_build_review_prompt', return_value='p'), \
             patch.object(garmin_server, 'call_llm',
                          side_effect=garmin_server.LLMQuotaError('429')):
            response = self.client.get('/api/training-review?force=1')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['_stale'])


class ActivityOverviewQuotaTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        garmin_server._ai_overview_locks.clear()
        self.client = garmin_server.app.test_client()
        login = self.client.post('/api/login',
                                 json={'username': 'hugo', 'password': 'test-password'})
        self.csrf = {'X-CSRF-Token': login.get_json()['csrfToken']}

    def test_quota_error_reports_429_not_a_server_error(self):
        with patch.object(garmin_server, 'llm_available', return_value=True), \
             patch.object(garmin_server, 'get_cache', return_value=None), \
             patch.object(garmin_server, '_activity_ai_detail', return_value={'id': 1}), \
             patch.object(garmin_server, '_activity_ai_plan_context', return_value=None), \
             patch.object(garmin_server, '_activity_ai_prompt', return_value='p'), \
             patch.object(garmin_server, 'call_llm',
                          side_effect=garmin_server.LLMQuotaError('429', retry_after=30.0)):
            response = self.client.post('/api/activities/42/ai-overview', headers=self.csrf)

        self.assertEqual(response.status_code, 429)
        payload = response.get_json()
        self.assertEqual(payload['code'], 'ai_quota_exceeded')
        self.assertEqual(payload['retryAfter'], 30.0)

    def test_concurrent_requests_generate_the_overview_only_once(self):
        store = {}
        calls = []

        def fake_get_cache(key, user_id=1):
            return store.get(key)

        def fake_set_cache(key, value, user_id=1):
            store[key] = (value, time.time())

        def slow_llm(*args, **kwargs):
            calls.append(1)
            time.sleep(0.3)  # håll låset så den andra tråden hinner köa
            return '{"tone":"good","headline":"Bra","summary":"Fint pass."}'

        results = []

        def hit():
            client = garmin_server.app.test_client()
            login = client.post('/api/login',
                                json={'username': 'hugo', 'password': 'test-password'})
            csrf = {'X-CSRF-Token': login.get_json()['csrfToken']}
            results.append(
                client.post('/api/activities/42/ai-overview', headers=csrf).status_code)

        with patch.object(garmin_server, 'llm_available', return_value=True), \
             patch.object(garmin_server, 'get_cache', side_effect=fake_get_cache), \
             patch.object(garmin_server, 'set_cache', side_effect=fake_set_cache), \
             patch.object(garmin_server, '_activity_ai_detail', return_value={'id': 42}), \
             patch.object(garmin_server, '_activity_ai_plan_context', return_value=None), \
             patch.object(garmin_server, '_activity_ai_prompt', return_value='p'), \
             patch.object(garmin_server, 'call_llm', side_effect=slow_llm):
            threads = [threading.Thread(target=hit) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        self.assertEqual(results, [200, 200])
        # Kärnan i fixen: två samtidiga anrop drar bara en kvotenhet.
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
