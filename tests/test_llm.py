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


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def gemini_payload(text):
    return {'candidates': [{'content': {'parts': [{'text': text}]}}]}


class LlmAdapterTests(unittest.TestCase):
    def test_gemini_request_shape_and_response(self):
        with mock.patch.object(garmin_server, 'LLM_PROVIDER', 'gemini'), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'), \
             mock.patch.object(garmin_server.requests, 'post',
                               return_value=FakeResponse(gemini_payload('Hej!'))) as post:
            reply = garmin_server.call_llm('Hur mår jag?', max_tokens=600, system='Var en coach.')

        self.assertEqual(reply, 'Hej!')
        url = post.call_args.args[0]
        kwargs = post.call_args.kwargs
        self.assertIn('generativelanguage.googleapis.com', url)
        self.assertIn('gemini-flash-latest', url)
        self.assertEqual(kwargs['headers']['x-goog-api-key'], 'test-key')
        body = kwargs['json']
        self.assertEqual(body['contents'][0]['parts'][0]['text'], 'Hur mår jag?')
        self.assertEqual(body['system_instruction']['parts'][0]['text'], 'Var en coach.')

    def test_gemini_error_raises(self):
        with mock.patch.object(garmin_server, 'LLM_PROVIDER', 'gemini'), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'), \
             mock.patch.object(garmin_server.requests, 'post',
                               return_value=FakeResponse({'error': {'code': 429, 'message': 'quota'}})):
            with self.assertRaises(RuntimeError) as ctx:
                garmin_server.call_llm('x')
        self.assertIn('429', str(ctx.exception))

    def test_gemini_empty_response_raises(self):
        with mock.patch.object(garmin_server, 'LLM_PROVIDER', 'gemini'), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'), \
             mock.patch.object(garmin_server.requests, 'post',
                               return_value=FakeResponse({'candidates': [{'finishReason': 'MAX_TOKENS'}]})):
            with self.assertRaises(RuntimeError) as ctx:
                garmin_server.call_llm('x')
        self.assertIn('MAX_TOKENS', str(ctx.exception))

    def test_anthropic_request_shape_and_response(self):
        with mock.patch.object(garmin_server, 'LLM_PROVIDER', 'anthropic'), \
             mock.patch.object(garmin_server, 'ANTHROPIC_KEY', 'sk-ant-test'), \
             mock.patch.object(garmin_server.requests, 'post',
                               return_value=FakeResponse({'content': [{'text': 'Hej från Claude'}]})) as post:
            reply = garmin_server.call_llm('Fråga', max_tokens=500)

        self.assertEqual(reply, 'Hej från Claude')
        url = post.call_args.args[0]
        kwargs = post.call_args.kwargs
        self.assertIn('api.anthropic.com', url)
        self.assertEqual(kwargs['json']['max_tokens'], 500)
        self.assertEqual(kwargs['headers']['x-api-key'], 'sk-ant-test')

    def test_llm_available_per_provider(self):
        with mock.patch.object(garmin_server, 'LLM_PROVIDER', 'gemini'), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', ''):
            self.assertFalse(garmin_server.llm_available())
        with mock.patch.object(garmin_server, 'LLM_PROVIDER', 'gemini'), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'nyckel'):
            self.assertTrue(garmin_server.llm_available())
        with mock.patch.object(garmin_server, 'LLM_PROVIDER', 'anthropic'), \
             mock.patch.object(garmin_server, 'ANTHROPIC_KEY', 'sk-ant-placeholder-x'):
            self.assertFalse(garmin_server.llm_available())
        with mock.patch.object(garmin_server, 'LLM_PROVIDER', 'anthropic'), \
             mock.patch.object(garmin_server, 'ANTHROPIC_KEY', 'sk-ant-riktig'):
            self.assertTrue(garmin_server.llm_available())

    def test_chat_endpoint_uses_adapter(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        client = garmin_server.app.test_client()
        login = client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})
        csrf = login.get_json()['csrfToken']

        with mock.patch.object(garmin_server, 'LLM_PROVIDER', 'gemini'), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'), \
             mock.patch.object(garmin_server.requests, 'post',
                               return_value=FakeResponse(gemini_payload('Kör ett lugnt pass idag.'))):
            response = client.post('/api/chat', json={'message': 'Vad ska jag träna idag?'},
                                   headers={'X-CSRF-Token': csrf})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['reply'], 'Kör ett lugnt pass idag.')


if __name__ == '__main__':
    unittest.main()
