"""Kontexten som dagens analys byggs av.

Tidigare bedömdes passet blint: prompten innehöll inget om sömn, belastning,
veckan eller atletens egna anteckningar. Testerna låser fast att kontexten
kommer med — och att en trasig datakälla aldrig sänker hela analysen.
"""
import os
import time
import unittest
from datetime import date
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


class RecoveryBlockTests(unittest.TestCase):
    def test_recovery_numbers_reach_the_prompt(self):
        with patch.object(garmin_server, '_recent_recovery', return_value=(64, 5.5)), \
             patch.object(garmin_server, 'latest_health_snapshot',
                          return_value={'restingHR': {'value': 47},
                                        'hrv': {'lastNightAvg': 68}}), \
             patch.object(garmin_server, '_load_context', return_value=(420, 1.42)), \
             patch.object(garmin_server, '_recent_activities', return_value=[]), \
             patch.object(garmin_server.strain_analysis, 'strain_summary',
                          return_value={'strain': 81, 'weekAvgStrain': 62,
                                        'headline': 'Tredje hårda dagen',
                                        'detail': 'Lägg in en lätt dag.'}):
            block = garmin_server._recovery_prompt_block(1)

        self.assertIn('CNS readiness: 64/100', block)
        self.assertIn('Sleep last night: 5.5 h', block)
        self.assertIn('Resting HR: 47 bpm', block)
        self.assertIn('HRV last night: 68 ms', block)
        self.assertIn('Chronic training load: 420', block)
        self.assertIn('1.42', block)
        self.assertIn('Strain today: 81/100', block)
        self.assertIn('Tredje hårda dagen', block)

    def test_a_broken_source_does_not_take_the_whole_block_down(self):
        # Sömndatan finns, belastningen kraschar. Analysen ska ändå bli av.
        with patch.object(garmin_server, '_recent_recovery', return_value=(70, 7.0)), \
             patch.object(garmin_server, 'latest_health_snapshot', return_value={}), \
             patch.object(garmin_server, '_load_context', side_effect=RuntimeError('db nere')), \
             patch.object(garmin_server, '_recent_activities', side_effect=RuntimeError('db nere')):
            block = garmin_server._recovery_prompt_block(1)

        self.assertIn('CNS readiness: 70/100', block)
        self.assertNotIn('Chronic training load', block)

    def test_missing_data_tells_the_model_not_to_speculate(self):
        with patch.object(garmin_server, '_recent_recovery', return_value=(None, None)), \
             patch.object(garmin_server, 'latest_health_snapshot', return_value={}), \
             patch.object(garmin_server, '_load_context', return_value=(None, None)), \
             patch.object(garmin_server, '_recent_activities', return_value=[]), \
             patch.object(garmin_server.strain_analysis, 'strain_summary',
                          side_effect=RuntimeError('ingen data')):
            block = garmin_server._recovery_prompt_block(1)

        self.assertIn('do not speculate', block)


class NotesBlockTests(unittest.TestCase):
    def test_notes_are_included_and_outrank_the_numbers(self):
        rows = [('Vaden känns stum efter intervallerna', 'skada'),
                ('Sov dåligt, mycket jobb', None)]
        with patch.object(garmin_server, 'db') as db:
            cur = db.return_value.__enter__.return_value.cursor.return_value
            cur.__enter__.return_value.fetchall.return_value = rows
            block = garmin_server._notes_prompt_block(1)

        self.assertIn('Vaden känns stum', block)
        self.assertIn('[skada]', block)
        self.assertIn('[note]', block)          # saknad kategori får ett namn
        self.assertIn('weigh these above the numbers', block)

    def test_no_notes_produces_no_block_at_all(self):
        with patch.object(garmin_server, 'db') as db:
            cur = db.return_value.__enter__.return_value.cursor.return_value
            cur.__enter__.return_value.fetchall.return_value = []
            self.assertEqual(garmin_server._notes_prompt_block(1), '')

    def test_a_database_error_is_swallowed(self):
        with patch.object(garmin_server, 'db', side_effect=RuntimeError('nere')):
            self.assertEqual(garmin_server._notes_prompt_block(1), '')


class ReviewResponseTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()
        self.client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})

    def test_the_wider_answer_is_passed_through_and_cached(self):
        answer = ('{"status":"done","headline":"För snabbt","body":"Kort.",'
                  '"assessment":"Du låg 45 s snabbare per km.",'
                  '"adjust":"Sänk tempot.","next":"Lugna 6 km."}')
        stored = {}
        with patch.object(garmin_server, 'llm_available', return_value=True), \
             patch.object(garmin_server, 'get_cache', return_value=None), \
             patch.object(garmin_server, 'set_cache',
                          side_effect=lambda k, v, u=1: stored.update(v)), \
             patch.object(garmin_server, '_build_review_prompt', return_value='p'), \
             patch.object(garmin_server, 'call_llm', return_value=answer):
            response = self.client.get('/api/training-review')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['assessment'], 'Du låg 45 s snabbare per km.')
        self.assertEqual(payload['adjust'], 'Sänk tempot.')
        self.assertEqual(payload['next'], 'Lugna 6 km.')
        self.assertEqual(stored['_review_version'], garmin_server.REVIEW_SCHEMA_VERSION)

    def test_an_answer_cached_under_the_old_schema_is_regenerated(self):
        # En v2-analys saknar de nya fälten; den far inte serveras som farsk.
        old = {'status': 'done', 'headline': 'Gammal', 'body': 'Kort.',
               '_review_version': 2}
        fresh = ('{"status":"done","headline":"Ny","body":"Kort.",'
                 '"assessment":"Motivering.","adjust":null,"next":"Lugnt."}')
        with patch.object(garmin_server, 'llm_available', return_value=True), \
             patch.object(garmin_server, 'get_cache', return_value=(old, time.time())), \
             patch.object(garmin_server, 'set_cache'), \
             patch.object(garmin_server, '_build_review_prompt', return_value='p'), \
             patch.object(garmin_server, 'call_llm', return_value=fresh) as llm:
            response = self.client.get('/api/training-review')

        llm.assert_called_once()
        self.assertEqual(response.get_json()['headline'], 'Ny')

    def test_an_old_cached_answer_is_still_good_enough_when_the_quota_is_gone(self):
        # Regenerering gar inte, men en v2-analys slar ett felmeddelande.
        old = {'status': 'done', 'headline': 'Gammal', 'body': 'Kort.',
               '_review_version': 2}
        with patch.object(garmin_server, 'llm_available', return_value=True), \
             patch.object(garmin_server, 'get_cache',
                          return_value=(old, time.time() - 3600)), \
             patch.object(garmin_server, '_build_review_prompt', return_value='p'), \
             patch.object(garmin_server, 'call_llm',
                          side_effect=garmin_server.LLMQuotaError('429')):
            response = self.client.get('/api/training-review')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['_stale'])


if __name__ == '__main__':
    unittest.main()
