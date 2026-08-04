import os
import re
import unittest
from pathlib import Path
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


ROOT = Path(__file__).resolve().parents[1]


class AssetVersionTests(unittest.TestCase):
    """Regression: en handbumpad cache-buster glömdes 2026-08-04, så webbläsaren
    körde ny HTML mot gammal app.js och klimatsidan stod och laddade. Versionen
    ska följa filinnehållet, inte någons minne."""

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

    def test_html_sources_carry_no_hand_written_version(self):
        for name in ('index.html', 'landing.html'):
            with self.subTest(name=name):
                html = (ROOT / 'public' / name).read_text(encoding='utf-8')
                stale = re.findall(r'\?v=(?!__ASSETV__)([^"\']+)', html)
                self.assertEqual(stale, [], f'hårdkodad assetversion i {name}: {stale}')

    def test_served_index_has_the_placeholder_substituted(self):
        self.login()
        html = self.client.get('/').get_data(as_text=True)

        self.assertNotIn('__ASSETV__', html)
        versions = re.findall(r'(?:app\.js|styles\.css)\?v=([0-9a-f]{12})', html)
        self.assertEqual(len(versions), 2)
        self.assertEqual(len(set(versions)), 1)

    def test_served_landing_has_the_placeholder_substituted(self):
        html = self.client.get('/').get_data(as_text=True)

        self.assertNotIn('__ASSETV__', html)
        self.assertRegex(html, r'landing\.js\?v=[0-9a-f]{12}')

    def test_version_follows_the_content_of_the_assets(self):
        before = garmin_server._asset_version()
        self.assertEqual(before, garmin_server._asset_version())

        real_read = Path.read_bytes

        def changed(self_path):
            data = real_read(self_path)
            return data + b'\n// nytt' if self_path.name == 'app.js' else data

        with mock.patch.object(Path, 'read_bytes', changed):
            after = garmin_server._asset_version()

        self.assertNotEqual(before, after, 'versionen ändras inte när app.js ändras')


if __name__ == '__main__':
    unittest.main()
