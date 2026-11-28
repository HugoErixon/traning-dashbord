import copy
import os
import unittest
from unittest import mock

import runner_profile


class ProfileValidationTests(unittest.TestCase):
    def test_empty_profile_does_not_invent_preferences(self):
        profile = runner_profile.normalize({})
        self.assertEqual(profile['preferredDays'], [])
        self.assertEqual(profile['hrZones'], [])
        self.assertEqual(profile['paceZones'], {})
        self.assertIsNone(profile['sessionsPerWeek'])

    def test_week_and_units_round_trip(self):
        data = {'preferredDays': [6, 1, 3, 1], 'longRunDay': 6,
                'weeklyKm': '32,5', 'paceZones': {'easy': {'fast': '5:10', 'slow': '6:00'}}}
        profile = runner_profile.normalize(data)
        self.assertEqual(profile['preferredDays'], [1, 3, 6])
        self.assertEqual(profile['weeklyKm'], 32.5)
        self.assertEqual(runner_profile.normalize(profile), profile)
        self.assertEqual(runner_profile.payload(profile)['summary']['paceRanges']['easy'],
                         {'fastSecPerKm': 310, 'slowSecPerKm': 360})

    def test_invalid_shape_days_numbers_and_ranges_rejected(self):
        for data in [None, [], {'preferredDays': [True]}, {'preferredDays': [7]},
                     {'longRunDay': 6}, {'weeklyKm': 'NaN'}, {'maxHr': 'inf'},
                     {'sessionsPerWeek': 2.5}, {'displayName': []},
                     {'maxHr': 100, 'restingHr': 110}, {'experience': 'unknown'},
                     {'hrZones': [None] * 5}, {'paceZones': []},
                     {'paceZones': {'easy': {'fast': '5:60', 'slow': '6:00'}}},
                     {'paceZones': {'easy': {'fast': '6:00', 'slow': '5:00'}}},
                     {'paceZones': {'easy': {'fast': '5:00'}}}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                runner_profile.normalize(data)

    def test_hr_zones_are_contiguous_and_bounded(self):
        zones = [{'min': low, 'max': high} for low, high in
                 [(90, 119), (120, 139), (140, 159), (160, 179), (180, 195)]]
        self.assertEqual(runner_profile.normalize({'maxHr': 195, 'hrZones': zones})['hrZones'], zones)
        for low in (119, 121):
            invalid = copy.deepcopy(zones)
            invalid[1]['min'] = low
            with self.assertRaises(ValueError):
                runner_profile.normalize({'hrZones': invalid})
        with self.assertRaises(ValueError):
            runner_profile.normalize({'maxHr': 190, 'hrZones': zones})
        with self.assertRaises(ValueError):
            runner_profile.normalize({'hrZones': zones[:4]})


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'
from werkzeug.security import generate_password_hash
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
import garmin_server
from security import parse_users
from user_store import MemoryUserStore


class ProfileApiTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        old_store = garmin_server.USER_STORE
        self.addCleanup(setattr, garmin_server, 'USER_STORE', old_store)
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()
        self.cache = {}
        def read(key, user_id):
            return (copy.deepcopy(self.cache[(key, user_id)]), 0) if (key, user_id) in self.cache else None
        def write(key, value, user_id):
            self.cache[key, user_id] = copy.deepcopy(value)
        for name, effect in [('get_cache', read), ('set_cache', write)]:
            patch = mock.patch.object(garmin_server, name, side_effect=effect)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(garmin_server, 'call_llm', side_effect=AssertionError('Profile must never call AI'))
        patch.start()
        self.addCleanup(patch.stop)
        response = self.client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})
        self.headers = {'X-CSRF-Token': response.get_json()['csrfToken']}

    def put(self, data):
        return self.client.put('/api/runner-profile', json=data, headers=self.headers)

    def test_save_reload_and_clear(self):
        self.assertEqual(self.client.get('/api/runner-profile').status_code, 200)
        saved = self.put({'displayName': '<script>text</script>', 'preferredDays': [0, 5], 'longRunDay': 5})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.client.get('/api/runner-profile').get_json(), saved.get_json())
        self.assertEqual(self.put({}).get_json()['profile']['preferredDays'], [])

    def test_rejected_payload_does_not_overwrite_saved_profile(self):
        self.put({'displayName': 'A runner'})
        for body in [{'weeklyKm': 'NaN'}, [], None]:
            response = self.put(body)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/runner-profile').get_json()['profile']['displayName'], 'A runner')

    def test_authentication_and_csrf_required(self):
        self.assertEqual(self.client.put('/api/runner-profile', json={}).status_code, 403)
        anonymous = garmin_server.app.test_client()
        self.assertEqual(anonymous.get('/api/runner-profile').status_code, 401)
        self.assertEqual(anonymous.put('/api/runner-profile', json={}).status_code, 401)

    def test_profiles_do_not_leak_between_users(self):
        self.put({'displayName': 'First runner'})
        result = self.client.post('/api/users', json={'username': 'second', 'password': 'another-password'}, headers=self.headers)
        self.assertEqual(result.status_code, 201)
        other = garmin_server.app.test_client()
        login = other.post('/api/login', json={'username': 'second', 'password': 'another-password'}).get_json()
        self.assertEqual(other.get('/api/runner-profile').get_json()['profile']['displayName'], '')
        other.put('/api/runner-profile', json={'displayName': 'Second runner'}, headers={'X-CSRF-Token': login['csrfToken']})
        self.assertEqual(self.client.get('/api/runner-profile').get_json()['profile']['displayName'], 'First runner')

    def test_storage_failure_is_not_reported_as_success(self):
        with mock.patch.object(garmin_server, 'set_cache', side_effect=RuntimeError('unavailable')):
            self.assertEqual(self.put({}).status_code, 500)

    def test_reference_paces_do_not_generate_ai_proposals(self):
        with mock.patch.object(garmin_server, '_pace_context', return_value={'bands': {}}), \
             mock.patch.object(garmin_server, 'generate_pace_proposals', side_effect=AssertionError('AI called')):
            response = self.client.get('/api/training-paces')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json(), {'bands': {}})


if __name__ == '__main__':
    unittest.main()
