"""Två fel som gjorde att dashboarden beskrev fel dag.

1. Sömnen: Garmin publicerar dagens beredskap före natten är färdigsynkad, så
   hälsopayloaden faller tillbaka på gårdagens natt. Flaggan för det satt på
   `sleep.fallback`, men lästes som `payload['fallback']` — alltid None. Följden
   var att gårdagens natt skrevs in under dagens datum och sedan lästes som
   "i natt" av morgonrapporten och dagens analys.

2. Planen: när begäran nämnde "idag" tvingades *varje* ändring i coachens svar
   till dagens datum. Bad man om ett pass idag flyttades även helgens pass hit,
   veckan kollapsade till en dag och tömdes på 'missed' dagen efter.
"""
import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402


TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


def live_payload(source_date, fallback):
    """Formen /api/health bygger från Garmin: flaggan sitter på sömnblocket."""
    return {
        'date': TODAY,
        'sleep': {'totalSec': 13932, 'score': 47, 'sourceDate': source_date,
                  'fallback': fallback, 'levels': [{'x': 1}]},
        'hrv': {'lastNightAvg': 65},
        'restingHR': {'value': 46},
    }


def snapshot_payload():
    """Formen latest_health_snapshot bygger: flaggan sitter på hela payloaden."""
    return {
        'date': TODAY,
        'sourceDate': YESTERDAY,
        'fallback': True,
        'sleep': {'totalSec': 13932, 'score': 47, 'levels': []},
    }


class StaleNightDetectionTests(unittest.TestCase):
    def test_a_live_payload_that_fell_back_to_yesterday_is_caught(self):
        payload = live_payload(YESTERDAY, True)
        self.assertTrue(garmin_server.health_sleep_is_fallback(payload))
        self.assertEqual(garmin_server.health_sleep_source_date(payload), YESTERDAY)

    def test_tonights_own_sleep_is_not_a_fallback(self):
        payload = live_payload(TODAY, False)
        self.assertFalse(garmin_server.health_sleep_is_fallback(payload))
        self.assertEqual(garmin_server.health_sleep_source_date(payload), TODAY)

    def test_the_database_snapshot_shape_is_caught_too(self):
        self.assertTrue(garmin_server.health_sleep_is_fallback(snapshot_payload()))
        self.assertEqual(garmin_server.health_sleep_source_date(snapshot_payload()),
                         YESTERDAY)

    def test_nonsense_is_not_mistaken_for_a_fresh_night(self):
        self.assertFalse(garmin_server.health_sleep_is_fallback(None))
        self.assertFalse(garmin_server.health_sleep_is_fallback('nej'))
        self.assertIsNone(garmin_server.health_sleep_source_date(None))


class RecoveryReportsWhichNightTests(unittest.TestCase):
    def test_the_source_date_follows_the_numbers(self):
        with patch.object(garmin_server, 'get_cache',
                          return_value=(live_payload(YESTERDAY, True), 0)), \
             patch.object(garmin_server, '_cns_score_from_health', return_value=52):
            cns, sleep_h, sleep_date = garmin_server._recent_recovery(1)

        self.assertEqual((cns, sleep_h), (52, 3.87))
        self.assertEqual(sleep_date, YESTERDAY)

    def test_a_fresh_night_reports_today(self):
        with patch.object(garmin_server, 'get_cache',
                          return_value=(live_payload(TODAY, False), 0)), \
             patch.object(garmin_server, '_cns_score_from_health', return_value=93):
            _, _, sleep_date = garmin_server._recent_recovery(1)

        self.assertEqual(sleep_date, TODAY)


class ReviewPromptTests(unittest.TestCase):
    def block(self, recovery):
        with patch.object(garmin_server, '_recent_recovery', return_value=recovery), \
             patch.object(garmin_server, 'latest_health_snapshot', return_value={}), \
             patch.object(garmin_server, '_load_context', return_value=(None, None)), \
             patch.object(garmin_server, '_recent_activities', return_value=[]), \
             patch.object(garmin_server.strain_analysis, 'strain_summary',
                          side_effect=RuntimeError('ingen strain i test')):
            return garmin_server._recovery_prompt_block(1)

    def test_last_night_is_only_called_last_night_when_it_is(self):
        self.assertIn('Sleep last night: 7.4 h', self.block((88, 7.4, TODAY)))

    def test_yesterdays_night_is_labelled_and_the_model_is_told_not_to_use_it(self):
        block = self.block((52, 3.87, YESTERDAY))

        self.assertNotIn('Sleep last night', block)
        self.assertIn(YESTERDAY, block)
        self.assertIn('HAS NOT SYNCED', block)
        # Beredskapen är räknad på samma gamla natt och måste märkas likadant.
        self.assertIn('CNS readiness: 52/100 (computed from the night of', block)


class TodayPinningTests(unittest.TestCase):
    """Bara det efterfrågade passet pinnas till idag — inte hela omplaneringen."""

    def test_the_added_session_is_the_one_pinned(self):
        changes = [
            {'action': 'reschedule', 'session_id': 7, 'new_dow': 3,
             'new_title': 'Flyttat långpass'},
            {'action': 'add', 'new_title': 'Styrkepass', 'new_detail': 'Helkropp'},
        ]
        self.assertIs(garmin_server._change_to_pin_on_today(changes), changes[1])

    def test_without_an_add_the_coachs_first_change_is_pinned(self):
        changes = [
            {'action': 'modify', 'session_id': 3, 'new_title': 'Lugn distans'},
            {'action': 'reschedule', 'session_id': 4, 'new_dow': 5,
             'new_title': 'Zon 2-distans'},
        ]
        self.assertIs(garmin_server._change_to_pin_on_today(changes), changes[0])

    def test_the_rest_of_the_week_is_left_where_the_coach_put_it(self):
        # Precis fallet från produktionen: tre ändringar, tre olika dagar,
        # alla hamnade på samma dag och blev 'missed' dagen efter.
        changes = [
            {'action': 'modify', 'session_id': 1, 'new_title': 'Lugn distans'},
            {'action': 'reschedule', 'session_id': 2, 'new_dow': 2,
             'new_title': 'Zon 2-distans'},
            {'action': 'reschedule', 'session_id': 3, 'new_dow': 3,
             'new_title': 'Mellanlång distans'},
        ]
        pinned = garmin_server._change_to_pin_on_today(changes)

        self.assertEqual(sum(1 for c in changes if c is pinned), 1)
        self.assertEqual([c['new_dow'] for c in changes if c is not pinned], [2, 3])

    def test_a_plain_keep_is_never_pinned(self):
        self.assertIsNone(garmin_server._change_to_pin_on_today(
            [{'action': 'keep', 'session_id': 1}]))

    def test_nothing_to_pin_is_not_an_error(self):
        self.assertIsNone(garmin_server._change_to_pin_on_today([]))
        self.assertIsNone(garmin_server._change_to_pin_on_today(None))


class DailyRoutineTests(unittest.TestCase):
    """Rutinen samlar historik när något synkat, men rapporten väntar på sömnen."""

    def run_routine(self, sleep_ok, ready_ok, history_done=False, report_done=False):
        sleep = {'dailySleepDTO': {'sleepTimeSeconds': 27000 if sleep_ok else 0}}
        readiness = [{'score': 71 if ready_ok else None}]

        def cache(key, user_id=None):
            if key == 'last_daily_history' and history_done:
                return ({'date': TODAY}, 0)
            if key == 'morning_report_sent' and report_done:
                return ({'date': TODAY}, 0)
            return None

        client = type('C', (), {
            'get_training_readiness': lambda self, d: readiness,
            'get_sleep_data': lambda self, d: sleep,
        })()

        with patch.object(garmin_server, 'get_cache', side_effect=cache), \
             patch.object(garmin_server, 'set_cache'), \
             patch.object(garmin_server, 'clear_cache'), \
             patch.object(garmin_server, 'get_garmin', return_value=client), \
             patch.object(garmin_server, 'collect_health_history') as history, \
             patch.object(garmin_server, 'collect_metric_history'), \
             patch.object(garmin_server, 'maybe_send_morning_report') as report:
            garmin_server.maybe_run_daily_routine()
        return history, report

    def test_readiness_alone_collects_history_but_holds_the_report(self):
        history, report = self.run_routine(sleep_ok=False, ready_ok=True)

        history.assert_called_once()
        report.assert_not_called()

    def test_the_report_goes_out_once_the_night_is_there(self):
        history, report = self.run_routine(sleep_ok=True, ready_ok=True)

        history.assert_called_once()
        report.assert_called_once()

    def test_a_later_sync_still_sends_the_report_history_already_ran(self):
        # Utan detta gick rapporten förlorad för dagen: rutinen såg att
        # historiken redan var insamlad och återvände direkt.
        history, report = self.run_routine(sleep_ok=True, ready_ok=True,
                                           history_done=True)

        history.assert_not_called()
        report.assert_called_once()

    def test_nothing_runs_twice(self):
        history, report = self.run_routine(sleep_ok=True, ready_ok=True,
                                           history_done=True, report_done=True)

        history.assert_not_called()
        report.assert_not_called()

    def test_no_data_at_all_means_wait(self):
        history, report = self.run_routine(sleep_ok=False, ready_ok=False)

        history.assert_not_called()
        report.assert_not_called()


if __name__ == '__main__':
    unittest.main()
