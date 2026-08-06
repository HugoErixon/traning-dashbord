from datetime import date
import unittest

from adaptive_plan import AdaptivePlanStore, evaluate, snapshot_hash


TODAY = date(2026, 8, 6)


def snapshot(kind='threshold', **health):
    return {
        'session': {'id': 7, 'type': 'run', 'kind': kind, 'is_quality': kind in ('threshold', 'interval'),
                    'title': 'Tröskel 4×8 min', 'km': 12},
        'health': health,
        'checkin': {},
        'load': {},
    }


class AdaptiveDecisionTests(unittest.TestCase):
    def test_one_weak_signal_does_not_overreact(self):
        result = evaluate(snapshot('threshold', sleep_hours=6.2, sleep_stale=False,
                                   readiness=72), today=TODAY)
        self.assertEqual(result['action'], 'keep')

    def test_multiple_independent_signals_move_quality(self):
        data = snapshot('threshold', sleep_hours=5.2, sleep_stale=False,
                        readiness=31, hrv=42, hrv_baseline=60,
                        resting_hr=58, resting_hr_baseline=45)
        result = evaluate(data, today=TODAY)
        self.assertEqual(result['action'], 'reschedule')
        self.assertEqual(result['proposedChange']['suggestedDate'], '2026-08-07')

    def test_stale_sleep_is_never_used_as_last_night(self):
        result = evaluate(snapshot('threshold', sleep_hours=3.5, sleep_stale=True,
                                   readiness=75), today=TODAY)
        self.assertEqual(result['action'], 'keep')
        self.assertFalse(any(signal['key'] == 'sleep' for signal in result['signals']))
        self.assertFalse(result['dataQuality']['sleepFresh'])

    def test_illness_overrides_good_watch_numbers(self):
        data = snapshot('easy', sleep_hours=8.5, sleep_stale=False, readiness=90)
        data['checkin'] = {'illness': True, 'illness_symptoms': 'feber'}
        result = evaluate(data, today=TODAY)
        self.assertEqual(result['action'], 'rest')
        self.assertIn('Sjukdom', result['reasons'][0])

    def test_high_pain_overrides_good_watch_numbers(self):
        data = snapshot('easy', sleep_hours=8.5, sleep_stale=False, readiness=90)
        data['checkin'] = {'pain': 8, 'pain_area': 'vänster vad'}
        result = evaluate(data, today=TODAY)
        self.assertEqual(result['action'], 'rest')
        self.assertTrue(result['warnings'])

    def test_moderate_adversity_reduces_but_does_not_cancel(self):
        data = snapshot('threshold', sleep_hours=6, sleep_stale=False, readiness=46)
        result = evaluate(data, today=TODAY)
        self.assertEqual(result['action'], 'reduce')
        self.assertEqual(result['proposedChange']['km'], 8.4)

    def test_rest_day_stays_stable(self):
        data = snapshot()
        data['session'] = None
        result = evaluate(data, today=TODAY)
        self.assertEqual(result['action'], 'no_session')

    def test_severe_soreness_reduces_an_easy_session(self):
        data = snapshot('easy', readiness=80)
        data['checkin'] = {'soreness': 9}
        result = evaluate(data, today=TODAY)
        self.assertEqual(result['action'], 'reduce')

    def test_limited_time_shortens_instead_of_cancelling(self):
        data = snapshot('easy', readiness=75)
        data['session']['estimated_minutes'] = 70
        data['checkin'] = {'available_minutes': 35}
        result = evaluate(data, today=TODAY)
        self.assertEqual(result['action'], 'reduce')
        self.assertIn('35 minuter', result['reasons'][0])


class AdaptiveStoreTests(unittest.TestCase):
    def test_checkin_is_bounded_and_round_trips(self):
        store = AdaptivePlanStore()
        saved = store.save_checkin(1, TODAY, {'energy': 7, 'pain': 2, 'illness': False})
        self.assertEqual(saved['energy'], 7)
        self.assertEqual(store.get_checkin(1, TODAY)['pain'], 2)
        with self.assertRaises(ValueError):
            store.save_checkin(1, TODAY, {'energy': 11})

    def test_same_input_is_idempotent(self):
        store = AdaptivePlanStore()
        data = snapshot(readiness=70)
        decision = evaluate(data, today=TODAY)
        first = store.save_decision(1, TODAY, data, decision)
        second = store.save_decision(1, TODAY, data, decision)
        self.assertEqual(first['id'], second['id'])

    def test_snapshot_hash_is_order_independent(self):
        self.assertEqual(snapshot_hash({'a': 1, 'b': 2}), snapshot_hash({'b': 2, 'a': 1}))

    def test_string_false_is_not_saved_as_illness(self):
        store = AdaptivePlanStore()
        saved = store.save_checkin(1, TODAY, {'illness': 'false'})
        self.assertFalse(saved['illness'])


if __name__ == '__main__':
    unittest.main()
