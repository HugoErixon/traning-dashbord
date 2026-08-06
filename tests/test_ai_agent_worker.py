import importlib.util
from pathlib import Path
import unittest


WORKER_PATH = Path(__file__).resolve().parents[1] / 'tools' / 'trainyze_ai_agent.py'
SPEC = importlib.util.spec_from_file_location('trainyze_ai_agent', WORKER_PATH)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class AiAgentCommandTests(unittest.TestCase):
    def test_codex_uses_workspace_sandbox_without_removed_approval_flag(self):
        command, stdin_text = worker.provider_command('codex', 'test')
        self.assertEqual(command[:2], ['codex', 'exec'])
        self.assertIn('workspace-write', command)
        self.assertNotIn('--ask-for-approval', command)
        self.assertEqual(stdin_text, 'test')

    def test_claude_uses_streaming_noninteractive_mode(self):
        command, stdin_text = worker.provider_command('claude', 'test')
        self.assertEqual(command[:3], ['claude', '-p', 'test'])
        self.assertIn('stream-json', command)
        self.assertIn('--dangerously-skip-permissions', command)
        self.assertIsNone(stdin_text)


if __name__ == '__main__':
    unittest.main()
