import unittest

from strength_progression import (
    build_strength_recommendations,
    canonical_exercise,
    extract_strength_targets,
)


class StrengthProgressionTests(unittest.TestCase):
    def test_aliases_merge_swedish_and_english_names(self):
        self.assertEqual(canonical_exercise('Bench press'), 'bench_press')
        self.assertEqual(canonical_exercise('B\u00e4nkpress'), 'bench_press')
        self.assertEqual(canonical_exercise('Deadlift'), 'deadlift')
        self.assertEqual(canonical_exercise('Marklyft'), 'deadlift')

    def test_extracts_each_planned_prescription(self):
        targets = extract_strength_targets(
            'Kn\u00e4b\u00f6j 5\u00d75, marklyft 4\u00d74, b\u00e4nkpress 4\u00d76-8, plankan 3\u00d745 sek'
        )
        self.assertEqual([target['canonical'] for target in targets], [
            'squat', 'deadlift', 'bench_press', 'plank'
        ])
        self.assertEqual((targets[1]['sets'], targets[1]['reps']), (4, 4))
        self.assertEqual((targets[2]['reps'], targets[2]['repsMax']), (6, 8))
        self.assertEqual(targets[3]['unit'], 'seconds')

    def test_mixed_bench_sets_reduce_weight_for_more_volume(self):
        history = [
            {'id': 1, 'sessionId': 'today', 'date': '2026-07-11', 'exercise': 'B\u00e4nkpress', 'sets': 2, 'reps': '6', 'weight': 50, 'note': ''},
            {'id': 2, 'sessionId': 'today', 'date': '2026-07-11', 'exercise': 'B\u00e4nkpress', 'sets': 2, 'reps': '4', 'weight': 60, 'note': ''},
        ]
        rec = build_strength_recommendations('B\u00e4nkpress 5\u00d75', history, '2026-07-15')[0]
        self.assertEqual(rec['sets'], 5)
        self.assertEqual(rec['reps'], 5)
        self.assertEqual(rec['weight'], 52.5)
        self.assertEqual((rec['lastReps'], rec['lastRepsMax']), (4, 4))

    def test_lower_rep_target_advances_lat_pulldown_weight(self):
        history = [
            {'id': 1, 'sessionId': 'latest', 'date': '2026-07-11', 'exercise': 'Latsdrag', 'sets': 3, 'reps': '10', 'weight': 80, 'note': ''},
        ]
        rec = build_strength_recommendations('Latsdrag 4\u00d75', history, '2026-07-15')[0]
        self.assertEqual(rec['weight'], 85.0)

    def test_same_day_logs_do_not_change_that_sessions_prescription(self):
        history = [
            {'id': 1, 'sessionId': 'previous', 'date': '2026-07-08', 'exercise': 'Bench press', 'sets': 3, 'reps': '6', 'weight': 55, 'note': ''},
            {'id': 2, 'sessionId': 'previous', 'date': '2026-07-08', 'exercise': 'B\u00e4nkpress', 'sets': 1, 'reps': '3', 'weight': 55, 'note': ''},
            {'id': 3, 'sessionId': 'current', 'date': '2026-07-11', 'exercise': 'B\u00e4nkpress', 'sets': 2, 'reps': '4', 'weight': 60, 'note': ''},
        ]
        rec = build_strength_recommendations('B\u00e4nkpress 4\u00d76', history, '2026-07-11')[0]
        self.assertEqual(rec['lastDate'], '2026-07-08')
        self.assertEqual((rec['lastReps'], rec['lastRepsMax']), (3, 6))
        self.assertEqual(rec['weight'], 55.0)

    def test_pain_note_prevents_automatic_weight_recommendation(self):
        history = [
            {'id': 1, 'sessionId': 'latest', 'date': '2026-06-18', 'exercise': 'Squat', 'sets': 1, 'reps': '8', 'weight': 60,
             'note': 'Fick ont i baksida l\u00e5r'},
        ]
        rec = build_strength_recommendations('Kn\u00e4b\u00f6j 3\u00d78', history, '2026-07-15')[0]
        self.assertIsNone(rec['weight'])
        self.assertEqual(rec['confidence'], 'caution')
        self.assertIn('sm\u00e4rta', rec['prescription'])

    def test_unknown_weight_is_not_invented(self):
        rec = build_strength_recommendations('Benpress 3\u00d712', [], '2026-07-15')[0]
        self.assertIsNone(rec['weight'])
        self.assertIn('2 reps kvar', rec['prescription'])


if __name__ == '__main__':
    unittest.main()
