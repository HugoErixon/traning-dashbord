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
from activity_detail import normalize_activity_detail  # noqa: E402
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402


RAW = {
    'activityId': 42,
    'activityName': 'Tröskel runt sjön',
    'activityType': {'typeKey': 'running'},
    'startTimeLocal': '2026-08-02 08:30:00',
    'distance': 10000,
    'duration': 3000,
    'movingDuration': 2950,
    'averageHR': 156,
    'maxHR': 178,
    'elevationGain': 123,
    'avgPower': 301,
    'maxPower': 455,
    'activityTrainingLoad': 185,
    'aerobicTrainingEffect': 4.1,
    'anaerobicTrainingEffect': 1.4,
}


DETAILS = {
    'metricDescriptors': [
        {'metricsIndex': 0, 'key': 'sumElapsedDuration'},
        {'metricsIndex': 1, 'key': 'directHeartRate'},
        {'metricsIndex': 2, 'key': 'directSpeed'},
        {'metricsIndex': 3, 'key': 'directElevation'},
        {'metricsIndex': 4, 'key': 'directPower'},
        {'metricsIndex': 5, 'key': 'sumDistance'},
    ],
    'activityDetailMetrics': [
        {'metrics': [0, 120, 3.2, 15, 240, 0]},
        {'metrics': [60, 160, 4.0, 22, 320, 240]},
    ],
    'geoPolylineDTO': {'polyline': [
        {'lat': 59.0, 'lon': 10.0, 'altitude': 15, 'distanceInMeters': 0},
        {'lat': 59.001, 'lon': 10.002, 'altitude': 22, 'distanceInMeters': 240},
    ]},
}


class FakeGarmin:
    def get_activity(self, activity_id):
        return {'activityId': activity_id, 'summaryDTO': {
            'distance': 10000, 'duration': 3000, 'movingDuration': 2950,
        }}

    def get_activity_details(self, activity_id):
        return DETAILS

    def get_activity_splits(self, activity_id):
        return {'lapDTOs': [{
            'lapIndex': 1, 'distance': 1000, 'duration': 240,
            'movingDuration': 240, 'averageHR': 164, 'averagePower': 330,
            'elevationGain': 8,
        }]}

    def get_activity_hr_in_timezones(self, activity_id):
        return [{'zoneNumber': 4, 'secsInZone': 900, 'zoneLowBoundary': 165}]

    def get_activity_power_in_timezones(self, activity_id):
        return [{'zoneNumber': 3, 'secsInZone': 1200, 'zoneLowBoundary': 280}]

    def get_activity_weather(self, activity_id):
        return {'temp': 18, 'relativeHumidity': 65,
                'weatherTypeDTO': {'desc': 'klart'}}

    def get_activity_gear(self, activity_id):
        return [{'displayName': 'Tävlingsskor'}]


class ActivityNormalizerTests(unittest.TestCase):
    def test_combines_summary_charts_route_laps_and_zones(self):
        activity = normalize_activity_detail(
            RAW,
            activity=FakeGarmin().get_activity(42),
            details=DETAILS,
            splits=FakeGarmin().get_activity_splits(42),
            hr_zones=FakeGarmin().get_activity_hr_in_timezones(42),
            power_zones=FakeGarmin().get_activity_power_in_timezones(42),
            weather=FakeGarmin().get_activity_weather(42),
            gear=FakeGarmin().get_activity_gear(42),
        )

        self.assertEqual(activity['id'], 42)
        self.assertEqual(activity['overview']['pace'], 295.0)
        self.assertEqual(activity['series'][1]['pace'], 250.0)
        self.assertEqual(activity['series'][1]['heartRate'], 160)
        self.assertEqual(activity['route'][-1]['lat'], 59.001)
        self.assertEqual(activity['laps'][0]['pace'], 240.0)
        self.assertEqual(activity['heartRateZones'][0]['seconds'], 900)
        self.assertEqual(activity['weather']['temperature'], 18)
        self.assertEqual(activity['gear'][0]['name'], 'Tävlingsskor')

    def test_missing_optional_data_produces_empty_panels(self):
        activity = normalize_activity_detail(RAW)

        self.assertEqual(activity['route'], [])
        self.assertEqual(activity['series'], [])
        self.assertEqual(activity['laps'], [])
        self.assertEqual(activity['heartRateZones'], [])
        self.assertIsNone(activity['weather'])


class ActivityDetailEndpointTests(unittest.TestCase):
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

    def test_endpoint_requires_login(self):
        self.assertEqual(self.client.get('/api/activities/42').status_code, 401)

    def test_an_activity_owned_by_someone_else_is_not_disclosed(self):
        self.login()
        with patch.object(garmin_server, '_stored_activity_for_user', return_value=None), \
             patch.object(garmin_server, 'get_garmin') as get_garmin:
            response = self.client.get('/api/activities/42')

        self.assertEqual(response.status_code, 404)
        get_garmin.assert_not_called()

    def test_returns_full_normalized_activity(self):
        self.login()
        with patch.object(garmin_server, '_stored_activity_for_user', return_value=RAW), \
             patch.object(garmin_server, 'get_cache', return_value=None), \
             patch.object(garmin_server, 'set_cache') as set_cache, \
             patch.object(garmin_server, '_garmin_connected', return_value=True), \
             patch.object(garmin_server, 'get_garmin', return_value=FakeGarmin()):
            response = self.client.get('/api/activities/42')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['source'], 'garmin')
        self.assertEqual(payload['activity']['name'], 'Tröskel runt sjön')
        self.assertEqual(payload['activity']['laps'][0]['averagePower'], 330)
        set_cache.assert_called_once()


if __name__ == '__main__':
    unittest.main()
