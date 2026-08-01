import os
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


class AcBedtimeTests(unittest.TestCase):
    def test_clock_normalization_accepts_colon_and_compact_input(self):
        self.assertEqual(garmin_server._normalize_clock('22:00'), '22:00')
        self.assertEqual(garmin_server._normalize_clock('2200'), '22:00')
        self.assertEqual(garmin_server._normalize_clock('900'), '09:00')

    def test_clock_normalization_rejects_invalid_times(self):
        for value in ('', '24:00', '22:60', '10:2', 'hello', None):
            with self.subTest(value=value):
                self.assertIsNone(garmin_server._normalize_clock(value))

    def test_frontend_uses_a_predictable_numeric_clock_field(self):
        index = (ROOT / 'public' / 'index.html').read_text(encoding='utf-8')
        app = (ROOT / 'public' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('id="ac-bedtime-input" inputmode="numeric"', index)
        self.assertNotIn('type="time" id="ac-bedtime-input"', index)
        self.assertIn('function normalizeClockInput(value)', app)


if __name__ == '__main__':
    unittest.main()
