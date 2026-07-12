import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = (ROOT / 'public' / 'index.html').read_text(encoding='utf-8')
        cls.app = (ROOT / 'public' / 'app.js').read_text(encoding='utf-8')

    def test_inline_event_handlers_are_not_used(self):
        source = self.index + '\n' + self.app
        inline_handler = re.compile(
            r'<[^>]+\son(?:click|keydown|keypress|input|change|blur|focus|mouseover|mouseout|submit)\s*=',
            re.IGNORECASE,
        )

        self.assertIsNone(inline_handler.search(source))

    def test_password_is_not_persisted_or_sent_as_header(self):
        self.assertNotIn("localStorage.setItem('sitePassword'", self.app)
        self.assertNotIn('x-site-password', self.app.lower())
        self.assertNotIn('x-site-user', self.app.lower())

    def test_user_and_ai_content_is_escaped_before_html_rendering(self):
        self.assertIn("escapeHtml(msg)", self.app)
        self.assertIn("const reply = escapeHtml(raw)", self.app)
        self.assertIn("escapeHtml(n.text)", self.app)
        self.assertIn("escapeHtml(it.title", self.app)
        self.assertIn("escapeHtml(ev.title)", self.app)


if __name__ == '__main__':
    unittest.main()
