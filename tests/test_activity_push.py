"""Notis när ett pass synkats in.

Den farligaste varianten här är inte att notisen uteblir utan att den kommer
femtio gånger: en backfill eller en första synk hittar hela historiken. Därför
handlar de flesta testerna om att hålla igen.
"""
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402


def activity(activity_id, hours_ago, name='Löppass', km=10.0, minutes=50):
    started = datetime.now() - timedelta(hours=hours_ago)
    return {
        'id': activity_id,
        'name': name,
        'date': started.strftime('%Y-%m-%d %H:%M:%S'),
        'type': 'running',
        'distance': km * 1000,
        'duration': minutes * 60,
        'raw': {'activityType': {'typeKey': 'running'}, 'duration': minutes * 60},
    }


class ActivityLineTests(unittest.TestCase):
    def test_a_run_shows_distance_time_and_pace(self):
        line = garmin_server._activity_push_line(activity(1, 1, km=10.0, minutes=50))
        self.assertIn('10,0 km', line)
        self.assertIn('50 min', line)
        self.assertIn('5:00/km', line)

    def test_a_long_session_is_shown_in_hours(self):
        line = garmin_server._activity_push_line(activity(1, 1, km=21.1, minutes=95))
        self.assertIn('1 h 35 min', line)

    def test_a_gym_session_without_distance_still_reads_well(self):
        line = garmin_server._activity_push_line(
            {'distance': 0, 'duration': 45 * 60, 'raw': {}})
        self.assertEqual(line, '45 min')
        self.assertNotIn('km', line)


class NotifyNewActivitiesTests(unittest.TestCase):
    def setUp(self):
        self.push = patch.object(garmin_server, 'push_available', return_value=True)
        self.push.start()
        self.addCleanup(self.push.stop)

    def send(self, activities, ids=None):
        sender = patch.object(garmin_server, 'send_push', return_value=1)
        mock = sender.start()
        self.addCleanup(sender.stop)
        with patch.object(garmin_server, '_recent_activities', return_value=activities), \
             patch.object(garmin_server.strain_analysis, 'session_verdict',
                          return_value={'headline': 'Lugnt'}):
            count = garmin_server.notify_new_activities(
                ids if ids is not None else [a['id'] for a in activities], 1)
        return mock, count

    def test_a_fresh_activity_produces_a_notification(self):
        send, count = self.send([activity(42, 1, name='Sotenäs Running')])
        self.assertEqual(count, 1)
        title, body = send.call_args[0][1], send.call_args[0][2]
        self.assertEqual(title, 'Sotenäs Running')
        self.assertIn('10,0 km', body)
        self.assertIn('Lugnt', body)

    def test_an_old_activity_appearing_late_is_not_announced(self):
        # Dyker upp forst nu men ar tre dagar gammal — da har dagen redan passerat.
        send, count = self.send([activity(42, 72)])
        self.assertEqual(count, 0)
        send.assert_not_called()

    def test_a_backfill_collapses_into_one_summary(self):
        many = [activity(i, 2, km=5.0) for i in range(1, 8)]
        send, count = self.send(many)
        self.assertEqual(count, 7)
        # En notis, inte sju.
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args[0][1], 'Nya pass synkade')
        self.assertIn('7 pass', send.call_args[0][2])
        self.assertIn('35,0 km', send.call_args[0][2])

    def test_two_activities_are_still_announced_individually(self):
        send, count = self.send([activity(1, 2, name='Morgonpass'),
                                 activity(2, 1, name='Kvällspass')])
        self.assertEqual(send.call_count, 2)
        self.assertEqual(count, 2)

    def test_each_activity_gets_its_own_tag_so_they_do_not_replace_each_other(self):
        send, _ = self.send([activity(11, 2), activity(22, 1)])
        tags = {call.kwargs['tag'] for call in send.call_args_list}
        self.assertEqual(tags, {'activity-11', 'activity-22'})
        urls = {call.kwargs['url'] for call in send.call_args_list}
        self.assertEqual(urls, {'/?activity=11&source=garmin', '/?activity=22&source=garmin'})

    def test_activities_we_did_not_ask_about_are_ignored(self):
        send, count = self.send([activity(1, 1), activity(2, 1)], ids=[1])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(count, 1)

    def test_nothing_happens_without_push_configured(self):
        self.push.stop()
        with patch.object(garmin_server, 'push_available', return_value=False), \
             patch.object(garmin_server, 'send_push') as send:
            self.assertEqual(garmin_server.notify_new_activities([1], 1), 0)
        send.assert_not_called()
        self.push.start()

    def test_an_empty_sync_sends_nothing(self):
        with patch.object(garmin_server, 'send_push') as send:
            self.assertEqual(garmin_server.notify_new_activities([], 1), 0)
        send.assert_not_called()

    def test_a_missing_verdict_does_not_lose_the_notification(self):
        sender = patch.object(garmin_server, 'send_push', return_value=1)
        mock = sender.start()
        self.addCleanup(sender.stop)
        with patch.object(garmin_server, '_recent_activities',
                          return_value=[activity(1, 1)]), \
             patch.object(garmin_server.strain_analysis, 'session_verdict',
                          side_effect=RuntimeError('ingen belastning')):
            count = garmin_server.notify_new_activities([1], 1)

        self.assertEqual(count, 1)
        self.assertIn('10,0 km', mock.call_args[0][2])


class ActivityIngestionTests(unittest.TestCase):
    def test_any_garmin_import_announces_before_the_activity_can_become_old(self):
        activities = [{'activityId': 42}]
        with patch.object(garmin_server, '_unseen_activity_ids', return_value={42}) as unseen, \
             patch.object(garmin_server, 'save_activities') as save, \
             patch.object(garmin_server, 'record_session_verdicts') as verdict, \
             patch.object(garmin_server, 'notify_new_activities', return_value=1) as notify:
            fresh = garmin_server.ingest_activities(activities, user_id=7)

        self.assertEqual(fresh, {42})
        unseen.assert_called_once_with(activities, 7)
        save.assert_called_once_with(activities, 7)
        verdict.assert_called_once_with({42}, 7)
        notify.assert_called_once_with({42}, 7)


if __name__ == '__main__':
    unittest.main()
