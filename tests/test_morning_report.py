"""Morgonrapporten: en notis per dag, när sömndatan väl landat.

Den hänger på den dagliga rutinen i stället för ett gissat klockslag, så
testerna handlar mest om att den inte går av för ofta, för sent, eller utan
underlag.
"""
import os
import unittest
from datetime import date, datetime, timedelta
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


def at_hour(hour):
    """En datetime.now() som ligger på en bestämd timme i lokal tid."""
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 3, hour, 30)
    return patch.object(garmin_server, 'datetime', Clock)


class MorningReportTextTests(unittest.TestCase):
    def plan_row(self, row):
        db = patch.object(garmin_server, 'db')
        mock = db.start()
        self.addCleanup(db.stop)
        cur = mock.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = row
        return mock

    def test_the_report_leads_with_the_session_and_carries_the_numbers(self):
        self.plan_row(('Intervaller', 10.0, 'run'))
        with patch.object(garmin_server, '_recent_recovery', return_value=(78, 7.25, TODAY)), \
             patch.object(garmin_server, '_load_context', return_value=(400, 1.0)), \
             patch.object(garmin_server, '_recent_activities', return_value=[]), \
             patch.object(garmin_server.strain_analysis, 'strain_summary',
                          return_value={'tone': 'good', 'headline': 'Produktiv'}):
            headline, body = garmin_server._morning_report_text(1)

        self.assertIn('Intervaller', headline)
        self.assertIn('10 km', headline)
        self.assertIn('7,2 h', body)
        self.assertIn('beredskap 78/100', body)
        # Ett positivt strain-omdome ska inte ta plats i notisen.
        self.assertNotIn('Produktiv', body)

    def test_a_warning_is_included_because_it_changes_the_day(self):
        self.plan_row(('Tröskelpass', 12.0, 'run'))
        with patch.object(garmin_server, '_recent_recovery', return_value=(52, 5.0, TODAY)), \
             patch.object(garmin_server, '_load_context', return_value=(400, 1.5)), \
             patch.object(garmin_server, '_recent_activities', return_value=[]), \
             patch.object(garmin_server.strain_analysis, 'strain_summary',
                          return_value={'tone': 'warn',
                                        'headline': 'Fjärde hårda dagen i rad'}):
            _, body = garmin_server._morning_report_text(1)

        self.assertIn('Fjärde hårda dagen i rad', body)

    def test_a_title_that_already_states_the_distance_is_left_alone(self):
        # Riktig plantitel fran produktionen; distansen stod dubbelt forr.
        self.plan_row(('Tröskelpass på löpband · 10 km', 10.0, 'run'))
        with patch.object(garmin_server, '_recent_recovery', return_value=(84, 9.9, TODAY)), \
             patch.object(garmin_server, '_load_context', return_value=(None, None)), \
             patch.object(garmin_server, '_recent_activities', return_value=[]), \
             patch.object(garmin_server.strain_analysis, 'strain_summary',
                          return_value={'tone': 'neutral'}):
            headline, _ = garmin_server._morning_report_text(1)

        self.assertEqual(headline, 'Tröskelpass på löpband · 10 km')
        self.assertEqual(headline.lower().count('km'), 1)

    def test_a_rest_day_still_produces_a_report(self):
        self.plan_row(None)
        with patch.object(garmin_server, '_recent_recovery', return_value=(80, 8.0, TODAY)), \
             patch.object(garmin_server, '_load_context', return_value=(None, None)), \
             patch.object(garmin_server, '_recent_activities', return_value=[]), \
             patch.object(garmin_server.strain_analysis, 'strain_summary',
                          return_value={'tone': 'neutral'}):
            headline, body = garmin_server._morning_report_text(1)

        self.assertEqual(headline, 'Vilodag')
        self.assertIn('8,0 h', body)

    def test_a_night_that_has_not_synced_is_not_reported_as_last_night(self):
        # Garmin publicerar beredskapen före sömnen. Rapporten läste då gårdagens
        # natt och skrev "Sov 3,9 h" på låsskärmen för en natt som inte var slut.
        self.plan_row(('Lugn löpning', 8.0, 'easy'))
        with patch.object(garmin_server, '_recent_recovery',
                          return_value=(52, 3.87, YESTERDAY)), \
             patch.object(garmin_server, '_load_context', return_value=(None, None)), \
             patch.object(garmin_server, '_recent_activities', return_value=[]), \
             patch.object(garmin_server.strain_analysis, 'strain_summary',
                          return_value={'tone': 'neutral'}):
            _, body = garmin_server._morning_report_text(1)

        self.assertNotIn('Sov', body)
        self.assertNotIn('3,9', body)

    def test_missing_recovery_does_not_break_the_report(self):
        self.plan_row(('Lugn löpning', 8.0, 'easy'))
        with patch.object(garmin_server, '_recent_recovery', side_effect=RuntimeError('nere')), \
             patch.object(garmin_server, '_load_context', side_effect=RuntimeError('nere')), \
             patch.object(garmin_server, '_recent_activities', side_effect=RuntimeError('nere')):
            headline, body = garmin_server._morning_report_text(1)

        self.assertIn('Lugn löpning', headline)
        self.assertIsNotNone(body)


class MorningReportSendingTests(unittest.TestCase):
    def test_nothing_is_sent_when_push_is_not_configured(self):
        with patch.object(garmin_server, 'push_available', return_value=False), \
             patch.object(garmin_server, 'send_push') as send:
            self.assertFalse(garmin_server.maybe_send_morning_report(1))
        send.assert_not_called()

    def test_it_only_fires_once_a_day(self):
        today = date.today().isoformat()
        with patch.object(garmin_server, 'push_available', return_value=True), \
             patch.object(garmin_server, 'get_cache', return_value=({'date': today}, 0)), \
             patch.object(garmin_server, 'send_push') as send:
            self.assertFalse(garmin_server.maybe_send_morning_report(1))
        send.assert_not_called()

    def test_an_evening_sync_does_not_produce_a_morning_report(self):
        with patch.object(garmin_server, 'push_available', return_value=True), \
             patch.object(garmin_server, 'get_cache', return_value=None), \
             patch.object(garmin_server, 'set_cache') as store, \
             patch.object(garmin_server, 'send_push') as send, \
             at_hour(20):
            self.assertFalse(garmin_server.maybe_send_morning_report(1))

        send.assert_not_called()
        # Dagen markeras som avklarad så den inte smäller vid nästa synk.
        self.assertEqual(store.call_args[0][1]['skipped'], 'outside_window')

    def test_it_waits_rather_than_report_a_night_that_has_not_synced(self):
        # Hälsocachen kan ligga kvar på gårdagen även när Garmin har i natt.
        # Dagen får inte markeras som avklarad — nästa synk ska ta rapporten.
        with patch.object(garmin_server, 'push_available', return_value=True), \
             patch.object(garmin_server, 'get_cache', return_value=None), \
             patch.object(garmin_server, 'set_cache') as store, \
             patch.object(garmin_server, '_recent_recovery',
                          return_value=(52, 3.87, YESTERDAY)), \
             patch.object(garmin_server, 'send_push') as send, \
             at_hour(7):
            self.assertFalse(garmin_server.maybe_send_morning_report(1))

        send.assert_not_called()
        store.assert_not_called()

    def test_a_morning_sync_sends_it_and_records_the_day(self):
        with patch.object(garmin_server, 'push_available', return_value=True), \
             patch.object(garmin_server, 'get_cache', return_value=None), \
             patch.object(garmin_server, 'set_cache') as store, \
             patch.object(garmin_server, '_recent_recovery', return_value=(78, 7.2, TODAY)), \
             patch.object(garmin_server, '_morning_report_text',
                          return_value=('Intervaller 10 km', 'Sov 7,2 h')), \
             patch.object(garmin_server, 'send_push', return_value=1) as send, \
             at_hour(7):
            self.assertTrue(garmin_server.maybe_send_morning_report(1))

        title, body = send.call_args[0][1], send.call_args[0][2]
        self.assertEqual(title, 'Intervaller 10 km')
        self.assertEqual(body, 'Sov 7,2 h')
        self.assertEqual(send.call_args.kwargs['tag'], 'morning-report')
        self.assertEqual(store.call_args[0][1]['date'], date.today().isoformat())

    def test_without_any_content_nothing_is_sent_and_the_day_stays_open(self):
        with patch.object(garmin_server, 'push_available', return_value=True), \
             patch.object(garmin_server, 'get_cache', return_value=None), \
             patch.object(garmin_server, 'set_cache') as store, \
             patch.object(garmin_server, '_recent_recovery', return_value=(78, 7.2, TODAY)), \
             patch.object(garmin_server, '_morning_report_text', return_value=(None, None)), \
             patch.object(garmin_server, 'send_push') as send, \
             at_hour(7):
            self.assertFalse(garmin_server.maybe_send_morning_report(1))

        send.assert_not_called()
        store.assert_not_called()


if __name__ == '__main__':
    unittest.main()
