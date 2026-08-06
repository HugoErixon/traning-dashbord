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
from activity_feedback import ActivityFeedbackStore  # noqa: E402
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402


class ActivityFeedbackApiTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.ACTIVITY_FEEDBACK_STORE = ActivityFeedbackStore()
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()

    def login(self):
        response = self.client.post('/api/login', json={
            'username': 'hugo', 'password': 'test-password'})
        return response.get_json()['csrfToken']

    def test_feedback_requires_login(self):
        self.assertEqual(self.client.get('/api/activities/42/feedback').status_code, 401)

    def test_feedback_requires_an_owned_activity(self):
        self.login()
        with patch.object(garmin_server, '_stored_activity_for_user', return_value=None):
            self.assertEqual(self.client.get('/api/activities/42/feedback').status_code, 404)

    def test_feedback_round_trips_and_requires_csrf(self):
        csrf = self.login()
        with patch.object(garmin_server, '_stored_activity_for_user', return_value={'activityId': 42}), \
             patch.object(garmin_server, 'clear_cache') as clear:
            rejected = self.client.put('/api/activities/42/feedback', json={'feeling': 4})
            saved = self.client.put('/api/activities/42/feedback', json={
                'feeling': 4, 'effort': 7, 'meal_before': 'normal',
                'hydration': 'good', 'notes': 'Kontrollerat pass',
            }, headers={'X-CSRF-Token': csrf})
            loaded = self.client.get('/api/activities/42/feedback')
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(loaded.get_json()['feedback']['feeling'], 4)
        clear.assert_called_once_with('activity-ai:v1:garmin:42', user_id=1)


if __name__ == '__main__':
    unittest.main()
