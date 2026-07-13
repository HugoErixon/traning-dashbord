import os
import unittest

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402


class GoalsApiTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server._TESTING_GOALS.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()
        self.csrf = self.login().get_json()['csrfToken']

    def login(self, username='hugo', password='test-password'):
        return self.client.post('/api/login', json={
            'username': username,
            'password': password,
        })

    def put_goal(self, payload):
        return self.client.put('/api/goals', json=payload,
                               headers={'X-CSRF-Token': self.csrf})

    def test_goal_is_null_until_set(self):
        response = self.client.get('/api/goals')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()['goal'])

    def test_save_and_read_back_goal(self):
        saved = self.put_goal({
            'goalTitle': 'Milen under 45 min',
            'goalDeadline': '2026-09-20',
            'currentBest': '48:30',
            'secondaryGoal': 'Styrka 2 pass/vecka',
        })

        self.assertEqual(saved.status_code, 200)
        goal = self.client.get('/api/goals').get_json()['goal']
        self.assertEqual(goal['goal_title'], 'Milen under 45 min')
        self.assertEqual(goal['goal_deadline'], '2026-09-20')
        self.assertEqual(goal['current_best'], '48:30')
        self.assertEqual(goal['secondary_goal'], 'Styrka 2 pass/vecka')
        self.assertTrue(goal['start_date'])

    def test_start_date_survives_goal_edits(self):
        self.put_goal({'goalTitle': 'Milen under 45 min'})
        original = self.client.get('/api/goals').get_json()['goal']['start_date']

        self.put_goal({'goalTitle': 'Milen under 44 min'})

        updated = self.client.get('/api/goals').get_json()['goal']
        self.assertEqual(updated['start_date'], original)
        self.assertEqual(updated['goal_title'], 'Milen under 44 min')

    def test_goal_validation(self):
        missing_title = self.put_goal({'goalTitle': ''})
        too_long = self.put_goal({'goalTitle': 'x' * 201})
        bad_deadline = self.put_goal({'goalTitle': 'Mål', 'goalDeadline': 'nästa år'})

        self.assertEqual(missing_title.status_code, 400)
        self.assertEqual(too_long.status_code, 400)
        self.assertEqual(bad_deadline.status_code, 400)

    def test_put_goal_requires_csrf(self):
        response = self.client.put('/api/goals', json={'goalTitle': 'Mål'})
        self.assertEqual(response.status_code, 403)

    def test_goals_are_per_user(self):
        self.put_goal({'goalTitle': 'Hugos mål'})
        self.client.post('/api/users', json={
            'username': 'frida', 'password': 'super-secret-1',
        }, headers={'X-CSRF-Token': self.csrf})
        self.client.post('/api/logout', headers={'X-CSRF-Token': self.csrf})

        frida_login = self.login('frida', 'super-secret-1')
        frida_csrf = frida_login.get_json()['csrfToken']

        self.assertIsNone(self.client.get('/api/goals').get_json()['goal'])

        self.client.put('/api/goals', json={'goalTitle': 'Fridas mål'},
                        headers={'X-CSRF-Token': frida_csrf})
        self.assertEqual(
            self.client.get('/api/goals').get_json()['goal']['goal_title'], 'Fridas mål')
        self.assertEqual(garmin_server._TESTING_GOALS[1]['goal_title'], 'Hugos mål')

    def test_goal_prompt_block(self):
        without_goal = garmin_server._goal_prompt_block(1)
        self.assertIn('No explicit goal', without_goal)

        garmin_server.save_user_goal(1, {
            'goal_title': 'Milen under 45 min',
            'goal_deadline': '2026-09-20',
            'current_best': '48:30',
            'secondary_goal': 'Styrka 2 pass/vecka',
            'start_date': '2026-07-01',
        })
        with_goal = garmin_server._goal_prompt_block(1)

        self.assertIn('GOAL: Milen under 45 min', with_goal)
        self.assertIn('Deadline: 2026-09-20', with_goal)
        self.assertIn('Current best: 48:30', with_goal)
        self.assertIn('SECONDARY GOAL: Styrka 2 pass/vecka', with_goal)


if __name__ == '__main__':
    unittest.main()
