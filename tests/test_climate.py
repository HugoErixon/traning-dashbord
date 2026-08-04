import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402
from security import parse_users  # noqa: E402
from user_store import MemoryUserStore  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


def _fake_db(rows):
    """Minimal db()-stand-in: en cursor som alltid svarar med rows."""
    cursor = mock.MagicMock()
    cursor.fetchall.return_value = rows
    cursor.__enter__ = lambda self: cursor
    cursor.__exit__ = lambda self, *exc: False
    conn = mock.MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda self: conn
    conn.__exit__ = lambda self, *exc: False
    return mock.MagicMock(return_value=conn)


class ClimateEndpointTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()
        garmin_server._sensor_roster.clear()

    def login(self):
        return self.client.post('/api/login', json={
            'username': 'hugo',
            'password': 'test-password',
        })

    def test_climate_requires_authentication(self):
        response = self.client.get('/api/climate')
        self.assertEqual(response.status_code, 401)

    def test_climate_reports_average_over_live_sensors(self):
        self.login()
        now = datetime.now(timezone.utc)
        rows = [
            ('Tempsensor_1', now - timedelta(minutes=2), 23.7, 51.7, 100, 152),
            ('Tempsensor_2', now - timedelta(minutes=3), 23.3, 51.5, 100, 120),
        ]
        with mock.patch.object(garmin_server, 'db', _fake_db(rows)):
            response = self.client.get('/api/climate')

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['available'])
        self.assertEqual(body['average']['sensor_count'], 2)
        self.assertAlmostEqual(body['average']['temperature_c'], 23.5, places=1)
        self.assertAlmostEqual(body['average']['humidity_pct'], 51.6, places=1)
        self.assertEqual([s['name'] for s in body['sensors']], ['Tempsensor_1', 'Tempsensor_2'])
        self.assertFalse(any(s['stale'] for s in body['sensors']))

    def test_silent_sensor_is_flagged_and_left_out_of_the_average(self):
        # Kärnan i ändringen: en sensor som slutat höra av sig ska synas som tyst,
        # inte tyst försvinna ur snittet. Tempsensor_3 låg nere i 44 dagar utan att
        # det märktes någonstans i gränssnittet.
        self.login()
        now = datetime.now(timezone.utc)
        rows = [
            ('Tempsensor_1', now - timedelta(minutes=2), 20.0, 50.0, 100, 152),
            ('Tempsensor_3', now - timedelta(days=44), 30.0, 90.0, 100, 90),
        ]
        with mock.patch.object(garmin_server, 'db', _fake_db(rows)):
            response = self.client.get('/api/climate')

        body = response.get_json()
        by_name = {s['name']: s for s in body['sensors']}
        self.assertFalse(by_name['Tempsensor_1']['stale'])
        self.assertTrue(by_name['Tempsensor_3']['stale'])
        self.assertEqual(body['average']['sensor_count'], 1)
        self.assertAlmostEqual(body['average']['temperature_c'], 20.0, places=1)

    def test_known_sensor_without_readings_still_appears(self):
        self.login()
        garmin_server._sensor_roster.update({
            'Tempsensor_3': {'model': 'SNZB-02P', 'vendor': 'SONOFF', 'description': ''},
        })
        with mock.patch.object(garmin_server, 'db', _fake_db([])):
            response = self.client.get('/api/climate')

        body = response.get_json()
        names = [s['name'] for s in body['sensors']]
        self.assertIn('Tempsensor_3', names)
        self.assertTrue(body['sensors'][0]['stale'])
        self.assertIsNone(body['average']['temperature_c'])

    def test_history_buckets_are_returned_for_the_graph(self):
        self.login()
        bucket = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        rows = [(bucket, 22.5, 48.0, 2)]
        with mock.patch.object(garmin_server, 'db', _fake_db(rows)), \
                mock.patch.object(garmin_server, '_get_outdoor_temperature_history', return_value=[]):
            response = self.client.get('/api/climate/history?hours=24')

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['hours'], 24)
        self.assertEqual(body['points'], [{'t': bucket.isoformat(), 'temp': 22.5, 'sensors': 2}])
        self.assertEqual(body['humidity_points'],
                         [{'t': bucket.isoformat(), 'humidity': 48.0, 'sensors': 2}])


class RemovedAcControlTests(unittest.TestCase):
    def setUp(self):
        garmin_server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        garmin_server.LOGIN_LIMITER.clear()
        garmin_server.USER_STORE = MemoryUserStore(parse_users(os.environ['USERS']))
        garmin_server.refresh_users()
        self.client = garmin_server.app.test_client()
        self.client.post('/api/login', json={'username': 'hugo', 'password': 'test-password'})

    def test_every_ac_route_reports_gone_rather_than_404(self):
        # Rutterna finns kvar med flit: en gammal öppen flik ska få veta att
        # funktionen är borttagen, inte se ut som ett driftfel. Utloggad träffar
        # man inloggningskravet först, vilket är rätt ordning — därav login i setUp.
        for path in ('/api/ac', '/api/ac/history', '/api/ac/loop', '/api/ac/bedtime'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 410)
                self.assertEqual(response.get_json()['code'], 'ac_removed')

    def test_ac_control_is_gone_from_the_frontend(self):
        index = (ROOT / 'public' / 'index.html').read_text(encoding='utf-8')
        app = (ROOT / 'public' / 'app.js').read_text(encoding='utf-8')
        for marker in ('toggle-ac-loop', 'set-ac-setpoint', 'send-ac-command',
                       'ac-manual-mode', 'ac-setpoint-input', 'ac-bedtime-input'):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, index)
                self.assertNotIn(marker, app)
        # Exakt '/api/ac' — '/api/activities' innehåller samma prefix.
        self.assertIsNone(re.search(r"/api/ac(?![a-z])", app))
        self.assertIn('/api/climate', app)


class MqttIngestTests(unittest.TestCase):
    def test_cached_startup_payload_is_ignored(self):
        # zigbee2mqtt återpublicerar sitt cachade läge vid uppstart. De saknar
        # linkquality, och att spara dem skulle datera om gamla värden till nu.
        message = mock.Mock(topic='zigbee2mqtt/Tempsensor_1',
                            payload=b'{"temperature":23.7,"humidity":51.7,"battery":100}')
        with mock.patch.object(garmin_server, '_store_sensor_reading') as store:
            garmin_server._on_mqtt_message(None, None, message)
        store.assert_not_called()

    def test_real_reading_is_stored(self):
        message = mock.Mock(
            topic='zigbee2mqtt/Tempsensor_1',
            payload=b'{"temperature":23.7,"humidity":51.7,"battery":100,"linkquality":152}')
        with mock.patch.object(garmin_server, '_store_sensor_reading') as store:
            garmin_server._on_mqtt_message(None, None, message)
        store.assert_called_once()
        self.assertEqual(store.call_args[0][0], 'Tempsensor_1')

    def test_bridge_topics_are_not_treated_as_sensors(self):
        message = mock.Mock(topic='zigbee2mqtt/bridge/health',
                            payload=b'{"temperature":1,"linkquality":10}')
        with mock.patch.object(garmin_server, '_store_sensor_reading') as store:
            garmin_server._on_mqtt_message(None, None, message)
        store.assert_not_called()

    def test_bridge_devices_builds_the_roster(self):
        payload = (b'[{"friendly_name":"Coordinator","type":"Coordinator"},'
                   b'{"friendly_name":"Tempsensor_3","type":"EndDevice",'
                   b'"definition":{"model":"SNZB-02P","vendor":"SONOFF","description":"x"}}]')
        message = mock.Mock(topic='zigbee2mqtt/bridge/devices', payload=payload)
        garmin_server._sensor_roster.clear()
        garmin_server._on_mqtt_message(None, None, message)
        self.assertEqual(list(garmin_server._sensor_roster), ['Tempsensor_3'])
        self.assertEqual(garmin_server._sensor_roster['Tempsensor_3']['model'], 'SNZB-02P')


if __name__ == '__main__':
    unittest.main()
