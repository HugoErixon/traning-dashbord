"""Ett coachsvar som redan är räknat får inte gå förlorat för att kopplingen bröts.

Svaret tar över tio sekunder att ta fram. På mobil räcker det att skärmen släcks
för att fetch ska avbrytas — servern har då redan svarat 200, men klienten ser
bara ett nätverksfel. Klienten skickar därför samma requestId när den försöker
igen, och servern ska lämna ut svaret den redan har i stället för att köra om
frågan. För en planändring är det inte bara en besparing: att köra om den skulle
tillämpa ändringen en andra gång mot ett schema som redan hunnit ändras.
"""
import datetime
import os
import threading
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


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.headers = {}
        self.text = ''

    def json(self):
        return self._payload


def gemini_payload(text):
    return {'candidates': [{'content': {'parts': [{'text': text}]}}]}


class MemoryCache:
    """Cachen i minnet, med samma form som get_cache/set_cache: (värde, tid)."""

    def __init__(self):
        self.store = {}

    def get(self, key, user_id=1):
        return self.store.get((user_id, key))

    def set(self, key, value, user_id=1):
        self.store[(user_id, key)] = (value, time.time())


class ReplayKeyTests(unittest.TestCase):
    def test_a_sane_key_is_accepted(self):
        self.assertEqual(garmin_server._assistant_replay_key('abc-123_XYZ'), 'abc-123_XYZ')

    def test_garbage_is_rejected_rather_than_used_as_a_cache_key(self):
        # Nyckeln kommer från klienten och blir en del av en databasnyckel.
        for bad in (None, '', '   ', 'a' * 65, 'key with space', 'a:b', '../../etc'):
            with self.subTest(bad=bad):
                self.assertIsNone(garmin_server._assistant_replay_key(bad))


class AssistantReplayTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server._assistant_inflight.clear()
        self.cache = MemoryCache()
        self.client = garmin_server.app.test_client()
        login = self.client.post('/api/login',
                                 json={'username': 'hugo', 'password': 'test-password'})
        self.csrf = login.get_json()['csrfToken']

    def post(self, payload, reply='Kör lugnt.'):
        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['gemini']), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'), \
             mock.patch.object(garmin_server, 'get_cache', self.cache.get), \
             mock.patch.object(garmin_server, 'set_cache', self.cache.set), \
             mock.patch.object(garmin_server.requests, 'post',
                               return_value=FakeResponse(gemini_payload(reply))) as sent:
            response = self.client.post('/api/assistant', json=payload,
                                        headers={'X-CSRF-Token': self.csrf})
        return response, sent

    def test_the_same_request_id_returns_the_answer_already_paid_for(self):
        payload = {'message': 'Vad ska jag träna idag?', 'requestId': 'abc123'}
        first, sent_first = self.post(payload)
        second, sent_second = self.post(payload, reply='Ett helt annat svar.')

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()['reply'], first.get_json()['reply'])
        self.assertEqual(sent_first.call_count, 1)
        # Andra försöket får inte kosta ett nytt leverantörsanrop.
        self.assertEqual(sent_second.call_count, 0)

    def test_a_new_question_gets_a_new_answer(self):
        self.post({'message': 'Vad ska jag träna idag?', 'requestId': 'abc123'})
        second, sent = self.post({'message': 'Och imorgon?', 'requestId': 'def456'},
                                 reply='Lugn distans.')

        self.assertEqual(second.get_json()['reply'], 'Lugn distans.')
        self.assertEqual(sent.call_count, 1)

    def test_without_a_request_id_nothing_is_stored_or_replayed(self):
        # Äldre klienter ska fungera precis som förut.
        first, sent_first = self.post({'message': 'Vad ska jag träna idag?'})
        second, sent_second = self.post({'message': 'Vad ska jag träna idag?'},
                                        reply='Ett helt annat svar.')

        self.assertEqual(first.get_json()['reply'], 'Kör lugnt.')
        self.assertEqual(second.get_json()['reply'], 'Ett helt annat svar.')
        self.assertEqual(sent_first.call_count, 1)
        self.assertEqual(sent_second.call_count, 1)
        self.assertFalse(self.cache.store)

    def test_a_failed_answer_is_not_replayed(self):
        # Ett fel kan bero på leverantören just då; nästa försök ska få chansen.
        with mock.patch.object(garmin_server, 'get_cache', self.cache.get), \
             mock.patch.object(garmin_server, 'set_cache', self.cache.set), \
             mock.patch.object(garmin_server, 'llm_available', return_value=False):
            failed = self.client.post('/api/assistant',
                                      json={'message': 'Hej', 'requestId': 'xyz789'},
                                      headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(failed.status_code, 503)
        self.assertFalse(self.cache.store)

        retried, sent = self.post({'message': 'Hej', 'requestId': 'xyz789'})
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(sent.call_count, 1)

    def test_a_dead_cache_costs_a_second_call_but_never_the_answer(self):
        def broken(*args, **kwargs):
            raise RuntimeError('databasen svarar inte')

        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['gemini']), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'), \
             mock.patch.object(garmin_server, 'get_cache', broken), \
             mock.patch.object(garmin_server, 'set_cache', broken), \
             mock.patch.object(garmin_server.requests, 'post',
                               return_value=FakeResponse(gemini_payload('Kör lugnt.'))):
            response = self.client.post('/api/assistant',
                                        json={'message': 'Hej', 'requestId': 'abc123'},
                                        headers={'X-CSRF-Token': self.csrf})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['reply'], 'Kör lugnt.')

    def test_a_resend_waits_for_the_call_already_running(self):
        # Utan den här väntan skulle en omsändning starta en andra planändring
        # mot ett schema som den första just höll på att skriva om.
        started = threading.Event()
        release = threading.Event()
        calls = []

        def slow_plan_request(message, history=None):
            calls.append(message)
            started.set()
            release.wait(timeout=5)
            return {'changes': 1, 'summary': 'Planen justerad: 1 flyttades.'}

        payload = {'message': 'Flytta passet till fredag', 'requestId': 'race1'}
        results = {}

        def run(label):
            with garmin_server.app.test_request_context():
                pass
            client = garmin_server.app.test_client()
            login = client.post('/api/login',
                                json={'username': 'hugo', 'password': 'test-password'})
            csrf = login.get_json()['csrfToken']
            results[label] = client.post('/api/assistant', json=payload,
                                         headers={'X-CSRF-Token': csrf})

        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['gemini']), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'), \
             mock.patch.object(garmin_server, 'get_cache', self.cache.get), \
             mock.patch.object(garmin_server, 'set_cache', self.cache.set), \
             mock.patch.object(garmin_server, '_is_plan_change_request', return_value=True), \
             mock.patch.object(garmin_server, '_apply_plan_request', slow_plan_request):
            first = threading.Thread(target=run, args=('first',))
            first.start()
            self.assertTrue(started.wait(timeout=5))
            second = threading.Thread(target=run, args=('second',))
            second.start()
            time.sleep(0.2)
            release.set()
            first.join(timeout=10)
            second.join(timeout=10)

        self.assertEqual(len(calls), 1, 'planändringen kördes två gånger')
        self.assertEqual(results['first'].status_code, 200)
        self.assertEqual(results['second'].status_code, 200)
        self.assertEqual(results['second'].get_json()['reply'],
                         results['first'].get_json()['reply'])


class AdaptiveBaselineDateTypeTests(unittest.TestCase):
    """health_history.date är TEXT. Ett date-objekt ger 'text >= date' och
    baslinjen försvinner tyst ur coachens underlag."""

    def test_the_baseline_query_compares_text_against_text(self):
        captured = {}

        class Cursor:
            def execute(self, sql, params=None):
                captured['sql'] = sql
                captured['params'] = params

            def fetchone(self):
                return (55, 48, 7.5)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class Conn:
            def cursor(self, *a, **kw):
                return Cursor()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch.object(garmin_server, 'db', lambda: Conn()), \
             mock.patch.object(garmin_server, 'get_cache', lambda *a, **kw: None), \
             mock.patch.object(garmin_server, 'latest_health_snapshot',
                               lambda *a, **kw: {}):
            result = garmin_server._adaptive_health_context(1)

        _, start, end = captured['params']
        for bound in (start, end):
            self.assertIsInstance(bound, str)
            self.assertFalse(isinstance(bound, datetime.date))
            datetime.date.fromisoformat(bound)
        self.assertEqual(result['hrv_baseline'], 55.0)
        self.assertEqual(result['resting_hr_baseline'], 48.0)


if __name__ == '__main__':
    unittest.main()
