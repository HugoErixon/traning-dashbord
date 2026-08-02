"""Webbnotiser: prenumerationer, utskick och städning av döda enheter."""
import json
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
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402


SUBSCRIPTION = {
    'endpoint': 'https://web.push.apple.com/abc123',
    'keys': {'p256dh': 'BPublicKey', 'auth': 'AuthSecret'},
}


class PushEndpointTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()
        login = self.client.post('/api/login',
                                 json={'username': 'hugo', 'password': 'test-password'})
        self.csrf = {'X-CSRF-Token': login.get_json()['csrfToken']}

    def test_subscribing_requires_a_session(self):
        anonymous = garmin_server.app.test_client()
        response = anonymous.post('/api/push/subscribe', json=SUBSCRIPTION)
        self.assertIn(response.status_code, (401, 403))

    def test_a_subscription_without_keys_is_rejected(self):
        response = self.client.post('/api/push/subscribe',
                                    json={'endpoint': 'https://x'}, headers=self.csrf)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'invalid_subscription')

    def test_a_valid_subscription_is_stored(self):
        with patch.object(garmin_server, 'db') as db:
            cur = db.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            response = self.client.post('/api/push/subscribe',
                                        json=SUBSCRIPTION, headers=self.csrf)
            sql, params = cur.execute.call_args[0]

        self.assertEqual(response.status_code, 200)
        self.assertIn('ON CONFLICT (endpoint) DO UPDATE', sql)
        self.assertEqual(params[0], SUBSCRIPTION['endpoint'])
        self.assertEqual(params[2], 'BPublicKey')

    def test_the_public_key_is_exposed_for_the_browser(self):
        with patch.object(garmin_server, 'VAPID_PUBLIC_KEY', 'pub'), \
             patch.object(garmin_server, 'VAPID_PRIVATE_KEY', 'priv'):
            payload = self.client.get('/api/push/key').get_json()
        self.assertEqual(payload['key'], 'pub')
        self.assertTrue(payload['available'])

    def test_a_test_notification_needs_a_registered_device(self):
        with patch.object(garmin_server, 'push_available', return_value=True), \
             patch.object(garmin_server, 'send_push', return_value=0):
            response = self.client.post('/api/push/test', headers=self.csrf)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['code'], 'no_devices')

    def test_push_that_is_not_configured_says_so_plainly(self):
        with patch.object(garmin_server, 'push_available', return_value=False):
            response = self.client.post('/api/push/test', headers=self.csrf)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['code'], 'push_not_configured')


class SendPushTests(unittest.TestCase):
    def rows(self, *endpoints):
        return [(e, 'BKey', 'Auth') for e in endpoints]

    def test_nothing_is_sent_when_keys_are_missing(self):
        with patch.object(garmin_server, 'VAPID_PRIVATE_KEY', ''):
            self.assertEqual(garmin_server.send_push(1, 'T', 'B'), 0)

    def test_the_payload_carries_title_body_and_url(self):
        with patch.object(garmin_server, 'VAPID_PUBLIC_KEY', 'pub'), \
             patch.object(garmin_server, 'VAPID_PRIVATE_KEY', 'priv'), \
             patch.object(garmin_server, 'db') as db, \
             patch.object(garmin_server, 'webpush') as webpush:
            cur = db.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchall.return_value = self.rows('https://push/1')
            sent = garmin_server.send_push(1, 'Dags att springa', 'Passet väntar', url='/plan')

        self.assertEqual(sent, 1)
        payload = json.loads(webpush.call_args.kwargs['data'])
        self.assertEqual(payload['title'], 'Dags att springa')
        self.assertEqual(payload['body'], 'Passet väntar')
        self.assertEqual(payload['url'], '/plan')

    def test_every_registered_device_gets_the_notification(self):
        with patch.object(garmin_server, 'VAPID_PUBLIC_KEY', 'pub'), \
             patch.object(garmin_server, 'VAPID_PRIVATE_KEY', 'priv'), \
             patch.object(garmin_server, 'db') as db, \
             patch.object(garmin_server, 'webpush') as webpush:
            cur = db.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchall.return_value = self.rows('https://push/1', 'https://push/2')
            sent = garmin_server.send_push(1, 'T', 'B')

        self.assertEqual(sent, 2)
        self.assertEqual(webpush.call_count, 2)

    def test_a_gone_subscription_is_deleted_rather_than_retried_forever(self):
        class Gone(Exception):
            response = type('R', (), {'status_code': 410})()

        with patch.object(garmin_server, 'VAPID_PUBLIC_KEY', 'pub'), \
             patch.object(garmin_server, 'VAPID_PRIVATE_KEY', 'priv'), \
             patch.object(garmin_server, 'WebPushException', Gone), \
             patch.object(garmin_server, 'db') as db, \
             patch.object(garmin_server, 'webpush', side_effect=Gone()), \
             patch.object(garmin_server, '_forget_subscription') as forget:
            cur = db.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchall.return_value = self.rows('https://push/dead')
            sent = garmin_server.send_push(1, 'T', 'B')

        self.assertEqual(sent, 0)
        forget.assert_called_once_with('https://push/dead')

    def test_a_temporary_failure_keeps_the_subscription(self):
        class ServerError(Exception):
            response = type('R', (), {'status_code': 500})()

        with patch.object(garmin_server, 'VAPID_PUBLIC_KEY', 'pub'), \
             patch.object(garmin_server, 'VAPID_PRIVATE_KEY', 'priv'), \
             patch.object(garmin_server, 'WebPushException', ServerError), \
             patch.object(garmin_server, 'db') as db, \
             patch.object(garmin_server, 'webpush', side_effect=ServerError()), \
             patch.object(garmin_server, '_forget_subscription') as forget:
            cur = db.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchall.return_value = self.rows('https://push/flaky')
            garmin_server.send_push(1, 'T', 'B')

        forget.assert_not_called()

    def test_one_dead_device_does_not_stop_the_others(self):
        class Gone(Exception):
            response = type('R', (), {'status_code': 404})()

        calls = []

        def maybe_fail(**kwargs):
            calls.append(kwargs['subscription_info']['endpoint'])
            if kwargs['subscription_info']['endpoint'].endswith('dead'):
                raise Gone()

        with patch.object(garmin_server, 'VAPID_PUBLIC_KEY', 'pub'), \
             patch.object(garmin_server, 'VAPID_PRIVATE_KEY', 'priv'), \
             patch.object(garmin_server, 'WebPushException', Gone), \
             patch.object(garmin_server, 'db') as db, \
             patch.object(garmin_server, 'webpush', side_effect=maybe_fail), \
             patch.object(garmin_server, '_forget_subscription'):
            cur = db.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchall.return_value = self.rows('https://push/dead', 'https://push/alive')
            sent = garmin_server.send_push(1, 'T', 'B')

        self.assertEqual(sent, 1)
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
