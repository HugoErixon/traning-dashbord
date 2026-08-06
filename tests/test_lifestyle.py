from datetime import date
import unittest

from lifestyle import LifestyleStore, analyze_impacts, behavior_value, normalize_entry


class LifestyleEntryTests(unittest.TestCase):
    def test_quantities_times_and_optional_boole_are_normalized(self):
        value = normalize_entry({'alcohol_drinks': '2', 'alcohol_last_time': '21:05',
                                 'protein_target': 'false', 'water_liters': '2.4'})
        self.assertEqual(value['alcohol_drinks'], 2)
        self.assertEqual(value['alcohol_last_time'], '21:05')
        self.assertFalse(value['protein_target'])
        self.assertEqual(value['water_liters'], 2.4)

    def test_invalid_amount_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_entry({'alcohol_drinks': 31})

    def test_late_caffeine_needs_time_when_caffeine_was_used(self):
        self.assertIsNone(behavior_value('late_caffeine', {'caffeine_servings': 2}))
        self.assertTrue(behavior_value('late_caffeine', {
            'caffeine_servings': 2, 'caffeine_last_time': '15:30'}))
        self.assertFalse(behavior_value('late_caffeine', {'caffeine_servings': 0}))

    def test_memory_store_is_scoped_by_user_and_date(self):
        store = LifestyleStore()
        store.save(1, date(2026, 8, 5), {'alcohol_drinks': 1})
        self.assertEqual(store.get(1, date(2026, 8, 5))['alcohol_drinks'], 1)
        self.assertEqual(store.get(2, date(2026, 8, 5)), {})


class LifestyleImpactTests(unittest.TestCase):
    @staticmethod
    def row(alcohol, sleep, hrv, rhr):
        return {'data': {'alcohol_drinks': alcohol},
                'outcome': {'sleep_score': sleep, 'hrv': hrv, 'resting_hr': rhr}}

    def test_requires_five_days_in_both_groups(self):
        result = analyze_impacts(
            [self.row(1, 60, 40, 60) for _ in range(5)] +
            [self.row(0, 80, 60, 45) for _ in range(4)])
        alcohol = next(item for item in result['insights'] if item['key'] == 'alcohol')
        self.assertFalse(alcohol['ready'])
        self.assertIsNone(alcohol['impact'])

    def test_reports_association_only_after_enough_contrast(self):
        result = analyze_impacts(
            [self.row(2, 60, 40, 60) for _ in range(5)] +
            [self.row(0, 80, 60, 45) for _ in range(5)])
        alcohol = next(item for item in result['insights'] if item['key'] == 'alcohol')
        self.assertTrue(alcohol['ready'])
        self.assertLess(alcohol['impact'], 0)
        self.assertFalse(result['causal'])


if __name__ == '__main__':
    unittest.main()
