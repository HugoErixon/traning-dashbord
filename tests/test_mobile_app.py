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
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests@127.0.0.1:1/unused-in-tests'

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
        self.assertIn('href="/app/manifest.json"', html)
        self.assertNotIn('__ASSETV__', html)
        versions = re.findall(r'(?:app\.js|styles\.css|ambient-fibers\.js)\?v=([0-9a-f]{12})', html)
        self.assertEqual(len(versions), 3)
        self.assertEqual(set(versions), {garmin_server.ASSET_VERSION})

    def test_app_without_slash_redirects(self):
        res = self.client.get('/app')
        self.assertIn(res.status_code, (301, 308))
        self.assertTrue(res.headers['Location'].endswith('/app/'))

    def test_app_assets_are_served(self):
        for path in ('/app/app.js', '/app/styles.css', '/app/ambient-fibers.js', '/app/manifest.json',
                     '/app/runner-profile.js', '/app/runner-profile.css',
                     '/app/kropp.js', '/app/kropp.css', '/app/morgon.js', '/app/morgon.css', '/app/intro.css',
                     '/app/icons/icon-192.png', '/app/icons/apple-touch-icon.png',
                     '/app/sw.js'):
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

    def test_app_assets_resolve_inside_app(self):
        # Kopian av webbsidan använder relativa sökvägar; de ska peka på filer i
        # public/app/, annars laddar appen tyst webbsidans versioner.
        html = (ROOT / 'public' / 'app' / 'index.html').read_text(encoding='utf-8')
        for ref in re.findall(r'(?:src|href)="(?!https?://|/|#|data:)([^"?]+)', html):
            with self.subTest(ref=ref):
                self.assertTrue((ROOT / 'public' / 'app' / ref).is_file(), f'{ref} saknas i public/app/')

    def test_app_assets_change_the_asset_version(self):
        before = garmin_server._asset_version()
        real_read = Path.read_bytes

        def changed(self_path):
            data = real_read(self_path)
            return data + b'\n/* nytt */' if self_path.parent.name == 'app' and self_path.name == 'styles.css' else data

        with mock.patch.object(Path, 'read_bytes', changed):
            after = garmin_server._asset_version()
        self.assertNotEqual(before, after, 'versionen ändras inte när appens css ändras')


    def test_body_tab_assets_are_versioned_and_load_after_app_js(self):
        html = self.client.get('/app/').get_data(as_text=True)
        for name in ('kropp.js', 'kropp.css'):
            with self.subTest(name=name):
                self.assertIn(f'{name}?v={garmin_server.ASSET_VERSION}', html)
        self.assertLess(html.index('app.js?v='), html.index('kropp.js?v='))

    def test_body_tab_pages_share_one_subnav(self):
        # Kropp = Läget, Sömn, Känningar och Vanor. Varje sida har samma undermeny
        # och NAV_SECTION låter Kropp-fliken lysa på alla fyra.
        html = (ROOT / 'public' / 'app' / 'index.html').read_text(encoding='utf-8')
        app_js = (ROOT / 'public' / 'app' / 'app.js').read_text(encoding='utf-8')
        pages = ('health', 'sleep', 'niggles', 'habits')
        for page in pages:
            with self.subTest(page=page):
                start = html.index(f'id="page-{page}"')
                subnav = html[html.index('<div class="subnav"', start):]
                subnav = subnav[:subnav.index('</div>')]
                self.assertEqual(re.findall(r'data-page="(\w+)"', subnav), list(pages))
                self.assertIn(f"{page}: 'health'", app_js)
        self.assertNotIn('id="page-journal"', html)


    def test_app_has_its_own_service_worker_scoped_to_app(self):
        # Notiser ska öppna hemskärmsappen, inte webbsidan i Safari.
        res = self.client.get('/app/sw.js')
        self.assertEqual(res.status_code, 200)
        self.assertIn('javascript', res.headers['Content-Type'])
        self.assertEqual(res.headers.get('Cache-Control'), 'no-cache')
        app_js = (ROOT / 'public' / 'app' / 'app.js').read_text(encoding='utf-8')
        self.assertIn("register('/app/sw.js', { scope: APP_SW_SCOPE })", app_js)
        self.assertIn("client: 'app'", app_js)

    def test_morning_view_is_reachable_from_start(self):
        html = (ROOT / 'public' / 'app' / 'index.html').read_text(encoding='utf-8')
        app_js = (ROOT / 'public' / 'app' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('id="page-morning"', html)
        self.assertIn("morning: 'home'", app_js)
        self.assertLess(html.index('kropp.js?v='), html.index('morgon.js?v='))


    def test_background_jobs_are_off_in_tests_and_can_be_turned_off(self):
        # En beta mot produktionens databas måste kunna köra utan egna jobb.
        self.assertFalse(garmin_server.BACKGROUND_JOBS)
        self.assertIsNone(garmin_server.scheduler)
        src = (ROOT / 'garmin_server.py').read_text(encoding='utf-8')
        self.assertIn("config.get('BACKGROUND_JOBS', 'true')", src)
        for guarded in ('if BACKGROUND_JOBS:\n    start_mqtt_listener()',
                        'if BACKGROUND_JOBS:\n    scheduler = BackgroundScheduler',
                        'if BACKGROUND_JOBS:\n    threading.Thread(target=_bootstrap_history'):
            with self.subTest(job=guarded):
                self.assertIn(guarded, src)


if __name__ == '__main__':
    unittest.main()
