import unittest

from activity_feedback import ActivityFeedbackStore, normalize_feedback


class ActivityFeedbackTests(unittest.TestCase):
    def test_normalizes_a_quick_post_workout_entry(self):
        value = normalize_feedback({
            'feeling': '4', 'effort': '7', 'meal_before': 'light',
            'hydration': 'good', 'notes': 'Bra ben',
        })
        self.assertEqual(value['feeling'], 4)
        self.assertEqual(value['effort'], 7)
        self.assertEqual(value['meal_before'], 'light')

    def test_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            normalize_feedback({'feeling': 6})
        with self.assertRaises(ValueError):
            normalize_feedback({'hydration': 'ocean'})

    def test_store_is_scoped_by_user_source_and_activity(self):
        store = ActivityFeedbackStore()
        store.save(1, 'garmin', 42, {'feeling': 5})
        self.assertEqual(store.get(1, 'garmin', 42)['feeling'], 5)
        self.assertEqual(store.get(2, 'garmin', 42), {})
        self.assertEqual(store.get(1, 'strava', 42), {})


if __name__ == '__main__':
    unittest.main()
