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
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402


def activity(day, load, activity_id=1, type_key='running'):
    return {
        'id': activity_id, 'name': 'Tröskelpass', 'date': day.isoformat(),
        'type': type_key, 'distance': 8000,
        'raw': {'activityTrainingLoad': load, 'activityType': {'typeKey': type_key}},
    }


class StrainEndpointTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()

    def login(self):
        return self.client.post('/api/login', json={
            'username': 'hugo', 'password': 'test-password',
        })

    def test_strain_requires_a_session(self):
        self.assertEqual(self.client.get('/api/strain').status_code, 401)

    def test_session_verdict_requires_a_session(self):
        self.assertEqual(self.client.get('/api/session-verdict').status_code, 401)

    def test_strain_reports_todays_cost_against_the_chronic_load(self):
        self.login()
        today = date.today()
        with patch.object(garmin_server, '_recent_activities',
                          return_value=[activity(today, 200)]), \
             patch.object(garmin_server, '_load_context', return_value=(100, 1.1)), \
             patch.object(garmin_server, '_recent_recovery', return_value=(75, 7.5, None)):
            response = self.client.get('/api/strain')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        # 200 mot en kronisk nivå på 100 = dubbel normaldos, strax under 90-ankaret.
        self.assertEqual(payload['strain'], 77)
        self.assertEqual(payload['referenceSource'], 'garmin')
        self.assertEqual(payload['balance']['state'], 'productive')

    def test_strain_survives_a_database_outage(self):
        self.login()
        with patch.object(garmin_server, '_recent_activities',
                          side_effect=RuntimeError('db down')):
            response = self.client.get('/api/strain')

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()['error'], 'Belastningen kunde inte beräknas.')


class SessionVerdictWritingTests(unittest.TestCase):
    def test_nothing_is_written_without_new_activities(self):
        self.assertEqual(garmin_server.record_session_verdicts(set()), 0)

    def test_old_activities_from_a_first_sync_are_skipped(self):
        stale = activity(date.today() - timedelta(days=20), 200, activity_id=7)
        with patch.object(garmin_server, '_recent_activities', return_value=[stale]):
            written = garmin_server.record_session_verdicts({7})

        self.assertEqual(written, 0)


if __name__ == '__main__':
    unittest.main()
