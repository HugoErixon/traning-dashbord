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
    def __init__(self, payload, status=200, headers=None, text=''):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


def gemini_payload(text):
    return {'candidates': [{'content': {'parts': [{'text': text}]}}]}


class LlmAdapterTests(unittest.TestCase):
    def test_gemini_request_shape_and_response(self):
        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['gemini']), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'), \
             mock.patch.object(garmin_server.requests, 'post',
                               return_value=FakeResponse(gemini_payload('Hej!'))) as post:
            reply = garmin_server.call_llm('Hur mår jag?', max_tokens=600, system='Var en coach.')

        self.assertEqual(reply, 'Hej!')
        url = post.call_args.args[0]
        kwargs = post.call_args.kwargs
        self.assertIn('generativelanguage.googleapis.com', url)
        self.assertIn('gemini-3.7-flash', url)
        self.assertEqual(kwargs['headers']['x-goog-api-key'], 'test-key')
        body = kwargs['json']
        self.assertEqual(body['contents'][0]['parts'][0]['text'], 'Hur mår jag?')
        self.assertEqual(body['generationConfig']['maxOutputTokens'], 600)
        self.assertEqual(body['generationConfig']['thinkingConfig']['thinkingLevel'], 'LOW')
        self.assertNotIn('responseMimeType', body['generationConfig'])
        self.assertEqual(body['system_instruction']['parts'][0]['text'], 'Var en coach.')

    def test_json_mode_is_forwarded_to_gemini(self):
        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['gemini']), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'), \
             mock.patch.object(garmin_server.requests, 'post',
                               return_value=FakeResponse(gemini_payload('{"ok": true}'))) as post:
            garmin_server.call_llm('Returnera JSON', json_mode=True)

        generation = post.call_args.kwargs['json']['generationConfig']
        self.assertEqual(generation['responseMimeType'], 'application/json')

    def test_groq_gpt_oss_uses_low_reasoning_and_json_mode(self):
        spec = {'kind': 'openai', 'key': 'test-key',
                'model': 'openai/gpt-oss-120b',
                'url': 'https://api.groq.com/openai/v1/chat/completions',
                'label': 'Groq'}
        response = FakeResponse({'choices': [{'message': {'content': '{"ok": true}'}}]})
        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['groq']), \
             mock.patch.object(garmin_server, '_provider_spec', return_value=spec), \
             mock.patch.object(garmin_server.requests, 'post', return_value=response) as post:
            garmin_server.call_llm('Returnera JSON', max_tokens=800, json_mode=True)

        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['reasoning_effort'], 'low')
        self.assertEqual(payload['reasoning_format'], 'hidden')
        self.assertEqual(payload['response_format'], {'type': 'json_object'})

    def test_model_aliases_resolved(self):
        with mock.patch.object(garmin_server, 'GEMINI_MODEL', 'gemini-flash-latest'):
            spec = garmin_server._provider_spec('gemini')
            self.assertEqual(spec['model'], 'gemini-flash-latest')
        with mock.patch.object(garmin_server, 'GEMINI_MODEL', 'gemini-2.0-flash'):
            spec = garmin_server._provider_spec('gemini')
            self.assertEqual(spec['model'], 'gemini-3.7-flash')
        with mock.patch.object(garmin_server, 'ANTHROPIC_MODEL', 'claude-3-5-sonnet-latest'):
            spec = garmin_server._provider_spec('anthropic')
            self.assertEqual(spec['model'], 'claude-sonnet-4-6')
        with mock.patch.dict(garmin_server.config, {'CEREBRAS_MODEL': 'llama-3.3-70b'}):
            spec = garmin_server._provider_spec('cerebras')
            self.assertEqual(spec['model'], 'gpt-oss-120b')

    def test_cloudflare_provider_uses_account_endpoint(self):
        with mock.patch.dict(garmin_server.config, {
                'CLOUDFLARE_ACCOUNT_ID': 'account-123',
                'CLOUDFLARE_API_TOKEN': 'token-123',
        }, clear=False):
            spec = garmin_server._provider_spec('cloudflare')

        self.assertEqual(spec['model'], '@cf/openai/gpt-oss-20b')
        self.assertEqual(spec['key'], 'token-123')
        self.assertEqual(
            spec['url'],
            'https://api.cloudflare.com/client/v4/accounts/account-123/ai/v1/chat/completions',
        )

    def test_gemini_error_raises(self):
        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['gemini']), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'), \
             mock.patch.object(garmin_server.requests, 'post',
                               return_value=FakeResponse({'error': {'code': 429, 'message': 'quota'}})):
            with self.assertRaises(RuntimeError) as ctx:
                garmin_server.call_llm('x')
        self.assertIn('429', str(ctx.exception))

    def test_gemini_empty_response_raises(self):
        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['gemini']), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'), \
             mock.patch.object(garmin_server.requests, 'post',
                               return_value=FakeResponse({'candidates': [{'finishReason': 'MAX_TOKENS'}]})):
            with self.assertRaises(RuntimeError) as ctx:
                garmin_server.call_llm('x')
        self.assertIn('MAX_TOKENS', str(ctx.exception))

    def test_anthropic_request_shape_and_response(self):
        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['anthropic']), \
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
        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['gemini']), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', ''):
            self.assertFalse(garmin_server.llm_available())
        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['gemini']), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'nyckel'):
            self.assertTrue(garmin_server.llm_available())
        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['anthropic']), \
             mock.patch.object(garmin_server, 'ANTHROPIC_KEY', 'sk-ant-placeholder-x'):
            self.assertFalse(garmin_server.llm_available())
        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['anthropic']), \
             mock.patch.object(garmin_server, 'ANTHROPIC_KEY', 'sk-ant-riktig'):
            self.assertTrue(garmin_server.llm_available())

    def test_assistant_endpoint_uses_adapter(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        client = garmin_server.app.test_client()
        login = client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})
        csrf = login.get_json()['csrfToken']

        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['gemini']), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'), \
             mock.patch.object(garmin_server.requests, 'post',
                               return_value=FakeResponse(gemini_payload('Kör ett lugnt pass idag.'))):
            response = client.post('/api/assistant', json={'message': 'Vad ska jag träna idag?'},
                                   headers={'X-CSRF-Token': csrf})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['reply'], 'Kör ett lugnt pass idag.')

    def test_assistant_can_serialize_cached_sleep_insights(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        client = garmin_server.app.test_client()
        login = client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})
        csrf = login.get_json()['csrfToken']
        cached = {
            'status': 'watch',
            'headline': 'Ojämn sömn',
            'insights': [{'title': 'Lång men svag', 'detail': 'Åtta timmar gav låg poäng.'}],
        }

        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['gemini']), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'), \
             mock.patch.object(garmin_server, 'get_cache',
                               return_value=(cached, garmin_server.time.time())), \
             mock.patch.object(garmin_server, '_build_sleep_coach',
                               return_value={'bedtime': '22:30'}), \
             mock.patch.object(garmin_server, '_recent_execution_block', return_value=''), \
             mock.patch.object(garmin_server, '_pace_context', return_value={}), \
             mock.patch.object(garmin_server, 'call_llm', return_value='Sov regelbundet.') as llm:
            response = client.post(
                '/api/assistant',
                json={'message': 'Varför är min sömn dålig?'},
                headers={'X-CSRF-Token': csrf},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['reply'], 'Sov regelbundet.')
        system_prompt = llm.call_args.kwargs['system']
        self.assertIn('SÖMNINSIKTER', system_prompt)
        self.assertIn('Ojämn sömn', system_prompt)

    def test_assistant_succeeds_even_if_sleep_insights_fail(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        client = garmin_server.app.test_client()
        login = client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})
        csrf = login.get_json()['csrfToken']

        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['gemini']), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'test-key'), \
             mock.patch.object(garmin_server, '_get_sleep_insights',
                               side_effect=RuntimeError('DB offline')), \
             mock.patch.object(garmin_server, '_build_sleep_coach',
                               return_value={'bedtime': '22:30'}), \
             mock.patch.object(garmin_server, '_recent_execution_block', return_value=''), \
             mock.patch.object(garmin_server, '_pace_context', return_value={}), \
             mock.patch.object(garmin_server, 'call_llm', return_value='Lägg dig i tid.') as llm:
            response = client.post(
                '/api/assistant',
                json={'message': 'Hur ska jag sova?'},
                headers={'X-CSRF-Token': csrf},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['reply'], 'Lägg dig i tid.')


if __name__ == '__main__':
    unittest.main()
