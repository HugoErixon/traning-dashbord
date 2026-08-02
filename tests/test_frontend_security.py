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
        self.assertIn("escapeHtml(j.text)", self.app)
        self.assertIn("escapeHtml(it.title", self.app)
        self.assertIn("escapeHtml(ev.title)", self.app)

    def test_activity_map_uses_attributed_openstreetmap_tiles(self):
        self.assertIn('https://tile.openstreetmap.org/', self.app)
        self.assertIn('https://www.openstreetmap.org/copyright', self.app)
        self.assertIn('© OpenStreetMap', self.app)

    def test_activity_map_supports_navigation_and_large_view(self):
        self.assertIn("addEventListener('pointermove'", self.app)
        self.assertIn("addEventListener('wheel'", self.app)
        self.assertIn("addEventListener('dblclick'", self.app)
        self.assertIn('activity-map-zoom-in', self.app)
        self.assertIn('activity-map-zoom-out', self.app)
        self.assertIn('activity-map-reset', self.app)
        self.assertIn('activity-map-expand', self.app)

    def test_strength_activity_has_exercise_view_instead_of_route_view(self):
        self.assertIn('function renderStrengthActivityDetail', self.app)
        self.assertIn('Övningar & set', self.app)
        self.assertIn('if (isStrengthActivity(activity))', self.app)
        self.assertIn('activity.strengthExercises', self.app)

    def test_integrations_live_on_a_dedicated_settings_page(self):
        self.assertIn('id="page-settings"', self.index)
        self.assertIn('id="settings-garmin-state"', self.index)
        self.assertIn('id="settings-strava-state"', self.index)
        self.assertIn('id="settings-calendar-state"', self.index)
        self.assertNotIn('class="garmin-sync-row"', self.index)
        self.assertNotIn('class="strava-sync-row"', self.index)
        self.assertIn("if (id === 'settings') loadSettingsPage()", self.app)

    def test_primary_navigation_has_settings_and_no_utility_buttons(self):
        navigation = self.index.split('<nav class="topnav"', 1)[1].split('</nav>', 1)[0]
        self.assertIn('data-page="settings"', navigation)
        self.assertNotIn('data-action="refresh-data"', navigation)
        self.assertNotIn('data-action="sync-calendar"', navigation)
        self.assertNotIn('data-action="logout"', navigation)

    def test_completed_home_session_opens_the_shared_activity_detail(self):
        self.assertIn('id="today-panel"', self.index)
        self.assertIn("panel.dataset.action = 'open-activity'", self.app)
        self.assertIn("panel.dataset.activitySource = longest.source === 'strava'", self.app)
        self.assertIn("card.classList.add('is-clickable')", self.app)


if __name__ == '__main__':
    unittest.main()
