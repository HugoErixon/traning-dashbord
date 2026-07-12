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


class UserAdminTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        # Nollställ användarlagret så tester inte läcker användare mellan varandra.
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()

    def login(self, username='hugo', password='test-password'):
        response = self.client.post('/api/login', json={
            'username': username,
            'password': password,
        })
        return response

    def create_user(self, csrf, username='frida', password='super-secret-1'):
        return self.client.post('/api/users', json={
            'username': username,
            'password': password,
        }, headers={'X-CSRF-Token': csrf})

    def test_admin_flag_present_in_login_and_session(self):
        login = self.login()
        self.assertTrue(login.get_json()['isAdmin'])

        session = self.client.get('/api/session')
        self.assertTrue(session.get_json()['isAdmin'])

    def test_admin_can_list_users(self):
        self.login()
        response = self.client.get('/api/users')

        self.assertEqual(response.status_code, 200)
        users = response.get_json()['users']
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]['username'], 'hugo')
        self.assertTrue(users[0]['isAdmin'])
        self.assertIn('garminConnected', users[0])

    def test_admin_can_create_user_who_can_log_in(self):
        csrf = self.login().get_json()['csrfToken']

        created = self.create_user(csrf)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()['username'], 'frida')

        self.client.post('/api/logout', headers={'X-CSRF-Token': csrf})
        login = self.login('frida', 'super-secret-1')
        self.assertEqual(login.status_code, 200)
        self.assertFalse(login.get_json()['isAdmin'])

    def test_non_admin_cannot_manage_users(self):
        csrf = self.login().get_json()['csrfToken']
        self.create_user(csrf)
        self.client.post('/api/logout', headers={'X-CSRF-Token': csrf})

        member_csrf = self.login('frida', 'super-secret-1').get_json()['csrfToken']
        listed = self.client.get('/api/users')
        created = self.create_user(member_csrf, username='eve', password='super-secret-2')

        self.assertEqual(listed.status_code, 403)
        self.assertEqual(created.status_code, 403)

    def test_create_user_validation(self):
        csrf = self.login().get_json()['csrfToken']

        duplicate = self.create_user(csrf, username='hugo', password='super-secret-1')
        bad_name = self.create_user(csrf, username='olle å', password='super-secret-1')
        short_password = self.create_user(csrf, username='olle', password='kort')

        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(bad_name.status_code, 400)
        self.assertEqual(short_password.status_code, 400)

    def test_create_user_requires_csrf(self):
        self.login()
        response = self.client.post('/api/users', json={
            'username': 'frida',
            'password': 'super-secret-1',
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'invalid_csrf_token')

    def test_admin_can_delete_user_but_not_self(self):
        csrf = self.login().get_json()['csrfToken']
        created_id = self.create_user(csrf).get_json()['id']

        delete_self = self.client.delete('/api/users/1', headers={'X-CSRF-Token': csrf})
        delete_member = self.client.delete(f'/api/users/{created_id}', headers={'X-CSRF-Token': csrf})
        delete_missing = self.client.delete(f'/api/users/{created_id}', headers={'X-CSRF-Token': csrf})

        self.assertEqual(delete_self.status_code, 400)
        self.assertEqual(delete_member.status_code, 200)
        self.assertEqual(delete_missing.status_code, 404)

        self.client.post('/api/logout', headers={'X-CSRF-Token': csrf})
        self.assertEqual(self.login('frida', 'super-secret-1').status_code, 401)


if __name__ == '__main__':
    unittest.main()
