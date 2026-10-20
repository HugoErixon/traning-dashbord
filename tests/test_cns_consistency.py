"""CNS-talet hoppade mellan ~95 och ~65 samma dag.

Sidan räknade CNS ur färsk Garmin-data, men synken tömmer hälsocachen var 15:e
minut och då räknade morgonrapport, coach och widget i stället ur
health_history. Där saknades Garmins beredskap och HRV-komponenten, och de
räknades som 50 — samma natt gav 91 live och 64 ur historiken.
"""
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


def payload(hrv=100, sleep=89, readiness=83, stress=27):
    return {
        'readiness': {'score': readiness},
        'hrv': {'component': hrv, 'lastNightAvg': 61},
        'sleep': {'score': sleep, 'totalSec': 29700},
        'stress': {'avg': stress},
    }


class CnsFormulaTests(unittest.TestCase):
    def test_complete_payload_uses_the_documented_weights(self):
        # 0.40·100 + 0.30·89 + 0.20·83 + 0.10·73 = 90.6
        self.assertEqual(garmin_server._cns_score_from_health(payload()), 91)

    def test_missing_parts_are_weighted_out_not_counted_as_fifty(self):
        partial = payload(hrv=None, readiness=None)
        # Bara sömn och stress: (0.30·89 + 0.10·73) / 0.40 = 85
        self.assertEqual(garmin_server._cns_score_from_health(partial), 85)

    def test_nothing_to_score_gives_none(self):
        self.assertIsNone(garmin_server._cns_score_from_health(
            payload(hrv=None, sleep=None, readiness=None, stress=None)))
        self.assertIsNone(garmin_server._cns_score_from_health({}))


class DailyReadinessEntryTests(unittest.TestCase):
    # Verklig form från Garmin 2026-10-20: osorterad, flera värden per dag.
    ENTRIES = [
        {'timestamp': '2026-10-20T03:49:16.0', 'score': 83, 'sleepScore': 89, 'inputContext': None},
        {'timestamp': '2026-10-20T17:17:34.0', 'score': 66, 'sleepScore': 89,
         'inputContext': 'AFTER_POST_EXERCISE_RESET'},
        {'timestamp': '2026-10-20T15:19:08.0', 'score': 90, 'sleepScore': 89,
         'inputContext': 'AFTER_WAKEUP_RESET'},
        {'timestamp': '2026-10-20T04:53:19.0', 'score': 75, 'sleepScore': None,
         'inputContext': 'UPDATE_REALTIME_VARIABLES'},
        {'timestamp': '2026-10-20T02:04:19.0', 'score': 74, 'sleepScore': None,
         'inputContext': 'AFTER_WAKEUP_RESET'},
    ]

    def test_morning_value_wins_regardless_of_list_order(self):
        for entries in (self.ENTRIES, list(reversed(self.ENTRIES))):
            self.assertEqual(garmin_server._daily_readiness_entry(entries)['score'], 83)

    def test_without_sleep_score_the_earliest_is_used(self):
        entries = [e | {'sleepScore': None} for e in self.ENTRIES]
        self.assertEqual(garmin_server._daily_readiness_entry(entries)['score'], 74)

    def test_empty_or_garbage_gives_empty_entry(self):
        self.assertEqual(garmin_server._daily_readiness_entry([]), {})
        self.assertEqual(garmin_server._daily_readiness_entry(None), {})
        self.assertEqual(garmin_server._daily_readiness_entry(['x', {'score': None}]), {})


class FakeCursor:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args):
        pass

    def fetchone(self):
        return self.row


class FakeConn(FakeCursor):
    def cursor(self, *args, **kwargs):
        return FakeCursor(self.row)


class SnapshotMatchesLiveTests(unittest.TestCase):
    def test_snapshot_carries_readiness_and_hrv_component(self):
        # date, sleep_score, sleep_hours, deep, rem, hrv_avg, rhr, bb, stress, readiness, hrv_component
        row = ('2026-10-20', 89, 8.25, 20, 22, 61, 51, 95, 27, 83, 100)
        with patch.object(garmin_server, 'db', return_value=FakeConn(row)):
            snap = garmin_server.latest_health_snapshot(1, '2026-10-20')

        self.assertEqual(snap['readiness']['score'], 83)
        self.assertEqual(snap['hrv']['component'], 100)
        self.assertEqual(snap['cns']['score'], garmin_server._cns_score_from_health(payload()))


class CurrentHealthTests(unittest.TestCase):
    def test_empty_cache_fetches_live_before_falling_back_to_history(self):
        live = payload()
        with patch.object(garmin_server, 'get_cache', return_value=None), \
             patch.object(garmin_server, '_garmin_connected', return_value=True), \
             patch.object(garmin_server, '_fetch_live_health', return_value=live) as fetch, \
             patch.object(garmin_server, 'latest_health_snapshot') as snapshot:
            self.assertIs(garmin_server._current_health(1), live)

        fetch.assert_called_once_with(1, 'hugo')
        snapshot.assert_not_called()

    def test_garmin_failure_falls_back_to_history(self):
        snap = payload(readiness=None)
        with patch.object(garmin_server, 'get_cache', return_value=None), \
             patch.object(garmin_server, '_garmin_connected', return_value=True), \
             patch.object(garmin_server, '_fetch_live_health', side_effect=RuntimeError('429')), \
             patch.object(garmin_server, 'latest_health_snapshot', return_value=snap):
            self.assertIs(garmin_server._current_health(1), snap)

    def test_cached_payload_is_used_without_calling_garmin(self):
        cached = payload()
        with patch.object(garmin_server, 'get_cache', return_value=(cached, 0)), \
             patch.object(garmin_server, '_fetch_live_health') as fetch:
            self.assertIs(garmin_server._current_health(1), cached)
        fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
