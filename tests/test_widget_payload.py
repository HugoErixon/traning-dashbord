import os
import unittest
from unittest.mock import patch

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402


class _FakeCursor:
    def execute(self, *args, **kwargs):
        pass

    def fetchone(self):
        return {}

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    def cursor(self, *args, **kwargs):
        return _FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


LIVE_HEALTH = {
    'sleep': {'score': 88, 'totalSec': 27000, 'sourceDate': '2026-08-02'},
    'hrv': {'lastNightAvg': 68, 'component': 70},
    'readiness': {'score': 74},
    'stress': {'avg': 20},
}

STORED_SNAPSHOT = {
    'sourceDate': '2026-08-01',
    'sleep': {'score': 62, 'totalSec': 19000},
    'hrv': {'lastNightAvg': 65, 'component': None},
    'readiness': {'score': None},
    'stress': {'avg': 28},
}


class MobileWidgetPayloadTests(unittest.TestCase):
    """The widget must carry data even when nobody has opened the site.

    /api/health is the only thing that fills the health cache, and run_sync
    clears it every three hours — so the cache alone leaves the widget blank.
    """

    def payload(self, cached=None, snapshot=None, snapshot_error=False):
        cache_row = (cached, 0) if cached is not None else None
        snap = (lambda *a, **k: (_ for _ in ()).throw(RuntimeError('db down'))) \
            if snapshot_error else (lambda *a, **k: snapshot)
        with patch.object(garmin_server, 'db', return_value=_FakeConn()), \
             patch.object(garmin_server, 'get_cache', return_value=cache_row), \
             patch.object(garmin_server, 'latest_health_snapshot', side_effect=snap):
            return garmin_server._mobile_widget_payload(1)

    def test_a_warm_cache_is_used_as_is(self):
        result = self.payload(cached=LIVE_HEALTH)

        self.assertEqual(result['source'], 'live')
        self.assertEqual(result['sleep']['score'], 88)
        self.assertIsNotNone(result['cns']['score'])

    def test_an_empty_cache_falls_back_to_stored_history(self):
        result = self.payload(cached=None, snapshot=STORED_SNAPSHOT)

        self.assertEqual(result['source'], 'history')
        self.assertEqual(result['sleep']['score'], 62)
        self.assertIsNotNone(result['cns']['score'])

    def test_a_hollow_cache_entry_also_falls_back(self):
        # run_sync leaves nothing behind; an empty dict must not count as data.
        result = self.payload(cached={}, snapshot=STORED_SNAPSHOT)

        self.assertEqual(result['source'], 'history')
        self.assertEqual(result['sleep']['score'], 62)

    def test_no_data_anywhere_is_reported_not_faked(self):
        result = self.payload(cached=None, snapshot=None)

        self.assertEqual(result['source'], 'none')
        self.assertIsNone(result['cns']['score'])
        self.assertIsNone(result['sleep']['score'])

    def test_a_failing_history_lookup_still_returns_a_payload(self):
        result = self.payload(cached=None, snapshot_error=True)

        self.assertEqual(result['source'], 'none')
        self.assertIn('weeklyVolume', result)

    def test_the_payload_always_carries_the_keys_the_widget_reads(self):
        for kwargs in ({'cached': LIVE_HEALTH},
                       {'cached': None, 'snapshot': STORED_SNAPSHOT},
                       {'cached': None, 'snapshot': None}):
            result = self.payload(**kwargs)
            for key in ('date', 'week', 'weeklyVolume', 'cns', 'sleep', 'nextQuality', 'source'):
                self.assertIn(key, result)


if __name__ == '__main__':
    unittest.main()
