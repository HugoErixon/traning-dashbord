import json
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


class MobileAppTests(unittest.TestCase):
    """Mobilappen (PWA) under /app/ är en egen frontend mot samma API."""

    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()

    def test_app_page_is_served_without_login(self):
        # Appen visar själv inloggningen; skyddet ligger i API:t.
        res = self.client.get('/app/')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('id="login-form"', html)
        self.assertNotIn('__ASSETV__', html)
        versions = re.findall(r'/app/app\.(?:js|css)\?v=([0-9a-f]{12})', html)
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0], garmin_server.ASSET_VERSION)

    def test_app_without_slash_redirects(self):
        res = self.client.get('/app')
        self.assertIn(res.status_code, (301, 308))
        self.assertTrue(res.headers['Location'].endswith('/app/'))

    def test_app_assets_are_served(self):
        for path in ('/app/app.js', '/app/app.css', '/app/manifest.json'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_manifest_scopes_the_pwa_to_app(self):
        manifest = json.loads((ROOT / 'public' / 'app' / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['start_url'], '/app/')
        self.assertEqual(manifest['scope'], '/app/')
        self.assertEqual(manifest['display'], 'standalone')

    def test_app_page_keeps_the_strict_security_headers(self):
        res = self.client.get('/app/')
        self.assertEqual(res.headers.get('X-Frame-Options'), 'DENY')
        self.assertIn("script-src 'self'", res.headers.get('Content-Security-Policy', ''))

    def test_app_html_has_no_inline_scripts(self):
        # CSP tillåter bara script från egen origin; ett inline-script skulle
        # tyst blockeras på iPhone.
        html = (ROOT / 'public' / 'app' / 'index.html').read_text(encoding='utf-8')
        self.assertEqual(re.findall(r'<script(?![^>]*\bsrc=)[^>]*>', html), [])

    def test_app_assets_change_the_asset_version(self):
        before = garmin_server._asset_version()
        real_read = Path.read_bytes

        def changed(self_path):
            data = real_read(self_path)
            return data + b'\n/* nytt */' if self_path.parent.name == 'app' and self_path.suffix == '.css' else data

        with mock.patch.object(Path, 'read_bytes', changed):
            after = garmin_server._asset_version()
        self.assertNotEqual(before, after, 'versionen ändras inte när appens css ändras')


if __name__ == '__main__':
    unittest.main()
