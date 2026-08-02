"""Leverantörskedjan: flera gratisnivåer staplade på varandra.

Kedjan ska gå vidare när en leverantör är slut på kvot, men aldrig när felet
är sådant att det upprepas överallt — och den ska aldrig av sig själv börja
använda en betald leverantör.
"""
import os
import unittest
from unittest.mock import patch

import requests
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


def gemini_ok(text='svar från gemini'):
    return FakeResponse({'candidates': [{'content': {'parts': [{'text': text}]}}]})


def gemini_429(delay=51.9):
    return FakeResponse({'error': {'code': 429, 'message': 'quota',
                                   'details': [{'retryDelay': f'{delay}s'}]}}, 429)


def openai_ok(text='svar från fallback'):
    return FakeResponse({'choices': [{'message': {'content': text}}]})


class ChainResolutionTests(unittest.TestCase):
    def test_a_paid_provider_is_never_added_on_its_own(self):
        # g3 har en Anthropic-nyckel liggande men ingen LLM_PROVIDERS satt.
        # Den får inte börja användas — det skulle kosta pengar utan att någon
        # bett om det.
        with patch.dict(garmin_server.config,
                        {'LLM_PROVIDERS': '', 'LLM_PROVIDER': ''}, clear=False), \
             patch.object(garmin_server, 'GEMINI_API_KEY', 'nyckel'):
            chain = garmin_server._resolve_llm_chain()
        self.assertEqual(chain, ['gemini'])

    def test_explicit_chain_is_honoured_in_order(self):
        with patch.dict(garmin_server.config,
                        {'LLM_PROVIDERS': 'gemini, cerebras ,groq'}, clear=False):
            self.assertEqual(garmin_server._resolve_llm_chain(),
                             ['gemini', 'cerebras', 'groq'])

    def test_unknown_and_duplicate_names_are_dropped(self):
        with patch.dict(garmin_server.config,
                        {'LLM_PROVIDERS': 'gemini,hittepå,gemini,groq'}, clear=False):
            self.assertEqual(garmin_server._resolve_llm_chain(), ['gemini', 'groq'])

    def test_legacy_single_provider_setting_still_works(self):
        with patch.dict(garmin_server.config,
                        {'LLM_PROVIDERS': '', 'LLM_PROVIDER': 'groq'}, clear=False):
            self.assertEqual(garmin_server._resolve_llm_chain(), ['groq'])


class ChainFailoverTests(unittest.TestCase):
    def setUp(self):
        garmin_server.reset_llm_cooldowns()
        self.specs = {
            'gemini': {'kind': 'gemini', 'key': 'g', 'model': 'gemini-test',
                       'label': 'Gemini'},
            'cerebras': {'kind': 'openai', 'key': 'c', 'model': 'llama-test',
                         'url': 'https://api.cerebras.ai/v1/chat/completions',
                         'label': 'Cerebras'},
        }

    def chain(self, names=('gemini', 'cerebras')):
        return patch.multiple(garmin_server,
                              LLM_CHAIN=list(names),
                              _provider_spec=lambda name: self.specs.get(name))

    def test_quota_on_the_first_provider_falls_through_to_the_second(self):
        with self.chain(), patch.object(requests, 'post',
                                        side_effect=[gemini_429(), openai_ok()]) as post:
            self.assertEqual(garmin_server.call_llm('p'), 'svar från fallback')
        self.assertEqual(post.call_count, 2)
        # Gemini ska nu vara nedkyld så nästa anrop slipper slösa en rundtur.
        self.assertGreater(garmin_server._llm_cooldown_remaining('gemini'), 40)

    def test_a_cooled_down_provider_is_skipped_entirely(self):
        garmin_server._set_llm_cooldown('gemini', 60)
        with self.chain(), patch.object(requests, 'post',
                                        side_effect=[openai_ok()]) as post:
            self.assertEqual(garmin_server.call_llm('p'), 'svar från fallback')
        # Ett enda anrop: Gemini frågades aldrig.
        self.assertEqual(post.call_count, 1)
        self.assertIn('cerebras.ai', post.call_args[0][0])

    def test_a_real_error_stops_the_chain_instead_of_burning_the_next_quota(self):
        broken = FakeResponse({'error': {'code': 400, 'message': 'trasig prompt'}}, 400)
        with self.chain(), patch.object(requests, 'post',
                                        side_effect=[broken, openai_ok()]) as post:
            with self.assertRaises(RuntimeError) as caught:
                garmin_server.call_llm('p')
        self.assertNotIsInstance(caught.exception, garmin_server.LLMQuotaError)
        self.assertEqual(post.call_count, 1)

    def test_network_failure_falls_through_and_cools_the_provider(self):
        with self.chain(), patch.object(
                requests, 'post',
                side_effect=[requests.ConnectionError('nere'), openai_ok()]) as post:
            self.assertEqual(garmin_server.call_llm('p'), 'svar från fallback')
        self.assertEqual(post.call_count, 2)
        self.assertGreater(garmin_server._llm_cooldown_remaining('gemini'), 0)

    def test_when_every_provider_is_out_of_quota_the_quota_error_surfaces(self):
        rate_limited = FakeResponse({}, 429, headers={'retry-after': '20'}, text='slut')
        with self.chain(), patch.object(requests, 'post',
                                        side_effect=[gemini_429(), rate_limited]):
            with self.assertRaises(garmin_server.LLMQuotaError):
                garmin_server.call_llm('p')

    def test_everything_cooled_down_still_gets_one_attempt(self):
        # Kvoten kan ha återställts tidigare än leverantören gissade; ett
        # försök är bättre än ett garanterat nej.
        garmin_server._set_llm_cooldown('gemini', 60)
        garmin_server._set_llm_cooldown('cerebras', 60)
        with self.chain(), patch.object(requests, 'post', side_effect=[gemini_ok()]) as post:
            self.assertEqual(garmin_server.call_llm('p'), 'svar från gemini')
        self.assertEqual(post.call_count, 1)

    def test_single_provider_chain_behaves_exactly_as_before(self):
        with self.chain(('gemini',)), patch.object(requests, 'post',
                                                   side_effect=[gemini_ok()]):
            self.assertEqual(garmin_server.call_llm('p'), 'svar från gemini')

    def test_system_prompt_is_forwarded_to_openai_compatible_providers(self):
        with self.chain(('cerebras',)), patch.object(
                requests, 'post', side_effect=[openai_ok()]) as post:
            garmin_server.call_llm('fråga', system='du är en coach')
        messages = post.call_args.kwargs['json']['messages']
        self.assertEqual(messages[0], {'role': 'system', 'content': 'du är en coach'})
        self.assertEqual(messages[1], {'role': 'user', 'content': 'fråga'})

    def test_unconfigured_providers_are_skipped(self):
        specs = dict(self.specs)
        specs['gemini'] = dict(specs['gemini'], key='')  # ingen nyckel
        with patch.multiple(garmin_server, LLM_CHAIN=['gemini', 'cerebras'],
                            _provider_spec=lambda name: specs.get(name)), \
             patch.object(requests, 'post', side_effect=[openai_ok()]) as post:
            self.assertEqual(garmin_server.call_llm('p'), 'svar från fallback')
        self.assertEqual(post.call_count, 1)


if __name__ == '__main__':
    unittest.main()


class ProviderAccountFailureTests(unittest.TestCase):
    """En leverantör kan vara obrukbar utan att vara trasig.

    Cerebras svarade 402 "Payment required" på varje modell med en giltig
    nyckel. Det får inte stoppa kedjan — men det är heller ingen idé att
    fråga igen om en halv minut.
    """

    def setUp(self):
        garmin_server.reset_llm_cooldowns()
        self.specs = {
            'cerebras': {'kind': 'openai', 'key': 'c', 'model': 'm',
                         'url': 'https://api.cerebras.ai/v1/chat/completions',
                         'label': 'Cerebras'},
            'gemini': {'kind': 'gemini', 'key': 'g', 'model': 'gemini-test',
                       'label': 'Gemini'},
        }

    def chain(self, names=('cerebras', 'gemini')):
        return patch.multiple(garmin_server, LLM_CHAIN=list(names),
                              _provider_spec=lambda name: self.specs.get(name))

    def test_payment_required_falls_through_instead_of_killing_the_chain(self):
        payment = FakeResponse({'message': 'Payment required'}, 402,
                               text='Payment required to access this resource.')
        with self.chain(), patch.object(requests, 'post',
                                        side_effect=[payment, gemini_ok()]) as post:
            self.assertEqual(garmin_server.call_llm('p'), 'svar från gemini')
        self.assertEqual(post.call_count, 2)

    def test_an_unusable_account_is_parked_for_a_long_time(self):
        payment = FakeResponse({}, 402, text='Payment required')
        with self.chain(), patch.object(requests, 'post',
                                        side_effect=[payment, gemini_ok()]):
            garmin_server.call_llm('p')
        # Betydligt längre än den vanliga nedkylningen på 30 s — det hjälper
        # inte att fråga igen förrän någon åtgärdat kontot.
        self.assertGreater(garmin_server._llm_cooldown_remaining('cerebras'), 600)

    def test_an_invalid_key_also_falls_through(self):
        unauthorized = FakeResponse({}, 401, text='Invalid API key')
        with self.chain(), patch.object(requests, 'post',
                                        side_effect=[unauthorized, gemini_ok()]) as post:
            self.assertEqual(garmin_server.call_llm('p'), 'svar från gemini')
        self.assertEqual(post.call_count, 2)

    def test_a_bad_request_still_stops_the_chain(self):
        # 400 är fortfarande vårt eget fel och ska inte kosta en andra kvot.
        bad = FakeResponse({'error': {'message': 'trasig prompt'}}, 400)
        with self.chain(), patch.object(requests, 'post',
                                        side_effect=[bad, gemini_ok()]) as post:
            with self.assertRaises(RuntimeError) as caught:
                garmin_server.call_llm('p')
        self.assertNotIsInstance(caught.exception, garmin_server.LLMUnavailableError)
        self.assertEqual(post.call_count, 1)

    def test_every_provider_unusable_surfaces_the_account_error(self):
        payment = FakeResponse({}, 402, text='Payment required')
        gemini_forbidden = FakeResponse(
            {'error': {'code': 403, 'message': 'API key not valid'}}, 403)
        with self.chain(), patch.object(requests, 'post',
                                        side_effect=[payment, gemini_forbidden]):
            with self.assertRaises(garmin_server.LLMUnavailableError):
                garmin_server.call_llm('p')
