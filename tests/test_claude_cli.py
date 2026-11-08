import json
import os
import subprocess
import unittest
from unittest import mock

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402


def cli_result(text='Svar', **extra):
    payload = {'type': 'result', 'subtype': 'success', 'is_error': False,
               'result': text, 'api_error_status': None}
    payload.update(extra)
    return subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr='')


SPEC = {'kind': 'claude_cli', 'key': '/usr/bin/claude', 'model': 'sonnet',
        'label': 'Claude Code'}


class ClaudeCliAdapterTests(unittest.TestCase):
    def setUp(self):
        garmin_server.reset_llm_cooldowns()

    def call(self, run_result, **kwargs):
        with mock.patch.object(garmin_server.subprocess, 'run',
                               return_value=run_result) as run:
            text = garmin_server._call_claude_cli(
                kwargs.pop('prompt', 'Hur ser veckan ut?'), 1024,
                kwargs.pop('system', 'Du är coach.'), 45, SPEC, False, **kwargs)
        return text, run

    def test_runs_without_tools_sessions_or_project_settings(self):
        text, run = self.call(cli_result('Lugnt idag.'))
        self.assertEqual(text, 'Lugnt idag.')
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ['/usr/bin/claude', '-p'])
        self.assertEqual(command[command.index('--tools') + 1], '')
        self.assertEqual(command[command.index('--setting-sources') + 1], '')
        for flag in ('--no-session-persistence', '--strict-mcp-config',
                     '--disable-slash-commands', '--system-prompt-file'):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index('--model') + 1], 'sonnet')
        self.assertEqual(run.call_args.kwargs['input'], 'Hur ser veckan ut?')

    def test_api_key_is_never_handed_to_the_cli(self):
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-ant-real',
                                          'CLAUDECODE': '1'}):
            _, run = self.call(cli_result())
        env = run.call_args.kwargs['env']
        self.assertNotIn('ANTHROPIC_API_KEY', env)
        self.assertNotIn('CLAUDECODE', env)

    def test_system_prompt_file_is_removed_afterwards(self):
        seen = {}

        def fake_run(command, **kwargs):
            path = command[command.index('--system-prompt-file') + 1]
            with open(path, encoding='utf-8') as handle:
                seen['system'] = handle.read()
            seen['path'] = path
            return cli_result()

        with mock.patch.object(garmin_server.subprocess, 'run', side_effect=fake_run):
            garmin_server._call_claude_cli('Fråga', 100, 'KONTEXT', 45, SPEC, False)
        self.assertEqual(seen['system'], 'KONTEXT')
        self.assertFalse(os.path.exists(seen['path']))

    def test_history_is_written_into_the_prompt(self):
        history = [{'role': 'user', 'content': 'Kan jag köra intervaller?'},
                   {'role': 'assistant', 'content': 'Ja, på torsdag.'}]
        _, run = self.call(cli_result(), prompt='Flytta det till fredag', history=history)
        prompt = run.call_args.kwargs['input']
        self.assertIn('Kan jag köra intervaller?', prompt)
        self.assertIn('[Coachen]\nJa, på torsdag.', prompt)
        self.assertTrue(prompt.endswith('Flytta det till fredag'))

    def test_leading_slash_cannot_become_a_cli_command(self):
        _, run = self.call(cli_result(), prompt='/login')
        self.assertEqual(run.call_args.kwargs['input'], ' /login')

    def test_json_schema_returns_structured_output(self):
        schema = {'type': 'object', 'properties': {'a': {'type': 'string'}}}
        text, run = self.call(cli_result('{"a":"x"}', structured_output={'a': 'x'}),
                              json_mode=True, json_schema=schema)
        self.assertEqual(json.loads(text), {'a': 'x'})
        command = run.call_args.args[0]
        self.assertEqual(json.loads(command[command.index('--json-schema') + 1]), schema)

    def test_usage_limit_is_quota(self):
        with self.assertRaises(garmin_server.LLMQuotaError) as ctx:
            self.call(cli_result("You've hit your usage limit · resets 3pm",
                                 is_error=True))
        self.assertEqual(ctx.exception.retry_after, garmin_server.CLAUDE_CLI_LIMIT_COOLDOWN)

    def test_logged_out_cli_is_unavailable(self):
        with self.assertRaises(garmin_server.LLMUnavailableError):
            self.call(cli_result('Not logged in · Please run /login', is_error=True,
                                 api_error_status=401))

    def test_timeout_and_garbage_are_transient(self):
        with mock.patch.object(garmin_server.subprocess, 'run',
                               side_effect=subprocess.TimeoutExpired('claude', 45)):
            with self.assertRaises(garmin_server.LLMTransientError):
                garmin_server._call_claude_cli('x', 10, None, 45, SPEC, False)
        with self.assertRaises(garmin_server.LLMTransientError):
            self.call(subprocess.CompletedProcess([], 1, stdout='', stderr='boom'))

    def test_bad_request_is_a_real_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.call(cli_result('Prompt is too long', is_error=True, api_error_status=400))
        self.assertNotIsInstance(ctx.exception, garmin_server.LLMTransientError)


class ClaudeCliChainTests(unittest.TestCase):
    def setUp(self):
        garmin_server.reset_llm_cooldowns()

    def test_aliases_resolve_to_one_provider(self):
        with mock.patch.dict(garmin_server.config,
                             {'LLM_PROVIDERS': 'claude-cli,claude_cli,gemini'}):
            self.assertEqual(garmin_server._resolve_llm_chain(), ['claude_cli', 'gemini'])

    def test_only_listed_users_may_use_the_subscription(self):
        with mock.patch.object(garmin_server, 'CLAUDE_CLI_BIN', '/bin/sh'), \
             mock.patch.object(garmin_server, 'CLAUDE_CLI_USERS', {1}):
            with garmin_server.app.test_request_context():
                garmin_server.flask_g.uid = 1
                self.assertTrue(garmin_server._provider_configured('claude_cli'))
                garmin_server.flask_g.uid = 7
                self.assertFalse(garmin_server._provider_configured('claude_cli'))

    def test_missing_binary_is_not_configured(self):
        with mock.patch.object(garmin_server, 'CLAUDE_CLI_BIN', '/nonexistent/claude'):
            self.assertFalse(garmin_server._provider_configured('claude_cli'))

    def test_other_users_fall_through_to_next_provider(self):
        cli = mock.Mock(return_value='från claude')
        gemini = mock.Mock(return_value='från gemini')
        with mock.patch.object(garmin_server, 'LLM_CHAIN', ['claude_cli', 'gemini']), \
             mock.patch.object(garmin_server, 'CLAUDE_CLI_BIN', '/bin/sh'), \
             mock.patch.object(garmin_server, 'GEMINI_API_KEY', 'g-key'), \
             mock.patch.dict(garmin_server._LLM_CALLERS, {'claude_cli': cli, 'gemini': gemini}):
            with garmin_server.app.test_request_context():
                garmin_server.flask_g.uid = 7
                self.assertEqual(garmin_server.call_llm('hej'), 'från gemini')
                cli.assert_not_called()
                garmin_server.flask_g.uid = 1
                self.assertEqual(garmin_server.call_llm('hej'), 'från claude')
