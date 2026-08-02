import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

import strava_integration


class StravaIntegrationTests(unittest.TestCase):
    def test_authorization_url_requests_private_activity_access_and_state(self):
        url = strava_integration.authorization_url('123', 'https://app.test/strava/callback', 'safe-state')
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, 'https')
        self.assertEqual(parsed.netloc, 'www.strava.com')
        self.assertEqual(query['client_id'], ['123'])
        self.assertEqual(query['redirect_uri'], ['https://app.test/strava/callback'])
        self.assertEqual(query['state'], ['safe-state'])
        self.assertIn('activity:read_all', query['scope'][0])

    def test_exchange_code_never_places_secret_in_url(self):
        response = Mock(ok=True)
        response.json.return_value = {'access_token': 'access', 'refresh_token': 'refresh'}
        with patch('strava_integration.requests.post', return_value=response) as request_post:
            payload = strava_integration.exchange_code('client', 'top-secret', 'code')
        self.assertEqual(payload['refresh_token'], 'refresh')
        args, kwargs = request_post.call_args
        self.assertEqual(args[0], strava_integration.TOKEN_URL)
        self.assertEqual(kwargs['data']['client_secret'], 'top-secret')

    def test_normalize_summary_matches_calendar_shape(self):
        summary = strava_integration.normalize_summary({
            'id': 44, 'name': 'Morgonpass', 'sport_type': 'Run',
            'start_date_local': '2026-08-02T07:30:00Z', 'distance': 10020,
            'moving_time': 3010, 'average_heartrate': 151,
            'total_elevation_gain': 87,
        })
        self.assertEqual(summary['activityId'], 44)
        self.assertEqual(summary['source'], 'strava')
        self.assertEqual(summary['activityType']['typeKey'], 'running')
        self.assertEqual(summary['distance'], 10020)

    def test_normalize_detail_includes_route_streams_laps_and_zones(self):
        activity = {
            'id': 44, 'name': 'Morgonpass', 'sport_type': 'Run',
            'start_date_local': '2026-08-02T07:30:00Z', 'distance': 1000,
            'moving_time': 300, 'elapsed_time': 320, 'athlete': {'id': 9},
            'laps': [{'lap_index': 1, 'distance': 1000, 'moving_time': 300,
                      'average_heartrate': 150}],
        }
        streams = {
            'time': {'data': [0, 60]}, 'distance': {'data': [0, 200]},
            'latlng': {'data': [[58.1, 11.2], [58.2, 11.3]]},
            'altitude': {'data': [4, 9]}, 'heartrate': {'data': [130, 150]},
            'watts': {'data': [210, 230]},
        }
        zones = [{'type': 'heartrate', 'distribution_buckets': [
            {'min': 100, 'max': 130, 'time': 80}, {'min': 130, 'max': 160, 'time': 220},
        ]}]
        detail = strava_integration.normalize_detail(activity, streams, zones)
        self.assertEqual(detail['_athleteId'], 9)
        self.assertEqual(len(detail['route']), 2)
        self.assertEqual(detail['series'][1]['pace'], 300)
        self.assertEqual(detail['laps'][0]['pace'], 300)
        self.assertEqual(detail['heartRateZones'][1]['seconds'], 220)

    def test_invalid_token_response_raises_safe_error(self):
        response = Mock(ok=False)
        response.json.return_value = {'message': 'Authorization Error'}
        with self.assertRaises(strava_integration.StravaError):
            strava_integration._token_response(response)


if __name__ == '__main__':
    unittest.main()
