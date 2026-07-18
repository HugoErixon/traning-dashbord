import os
import unittest

from werkzeug.security import generate_password_hash


os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server  # noqa: E402


HISTORY = [
    {'id': 1, 'sessionId': 'a', 'date': '2026-07-10', 'exercise': 'Knäböj', 'sets': 5, 'reps': '5', 'weight': 80, 'note': ''},
    {'id': 2, 'sessionId': 'a', 'date': '2026-07-10', 'exercise': 'Bänkpress', 'sets': 4, 'reps': '6', 'weight': 55, 'note': ''},
]


class SessionTypeValidationTests(unittest.TestCase):
    def test_valid_types_normalized(self):
        self.assertEqual(garmin_server._valid_session_type('lift'), 'lift')
        self.assertEqual(garmin_server._valid_session_type(' LIFT '), 'lift')
        self.assertEqual(garmin_server._valid_session_type('rest'), 'rest')

    def test_invalid_types_rejected(self):
        self.assertIsNone(garmin_server._valid_session_type(None))
        self.assertIsNone(garmin_server._valid_session_type('null'))
        self.assertIsNone(garmin_server._valid_session_type('yoga'))
        self.assertIsNone(garmin_server._valid_session_type(''))


class ConvertedLiftEnrichmentTests(unittest.TestCase):
    def test_generic_lift_detail_falls_back_to_history(self):
        """Ett löppass som gjorts om till generiskt gympass får ändå vikter från historiken."""
        session = {'id': 1, 'week': 29, 'dow': 3, 'type': 'lift',
                   'detail': 'Gympass · helkropp 45 min', 'title': 'Gympass'}

        garmin_server._enrich_strength_plan([session], user_id=1, history=HISTORY)

        recs = session['strength_recommendations']
        self.assertTrue(recs, 'fallback ska ge rekommendationer trots generisk detaljtext')
        exercises = {r['canonical'] for r in recs}
        self.assertIn('squat', exercises)
        self.assertIn('bench_press', exercises)
        self.assertTrue(session['strength_recommendation_text'])

    def test_named_exercises_still_use_detail(self):
        session = {'id': 2, 'week': 29, 'dow': 3, 'type': 'lift',
                   'detail': 'Knäböj 5×5', 'title': 'Styrka'}

        garmin_server._enrich_strength_plan([session], user_id=1, history=HISTORY)

        recs = session['strength_recommendations']
        self.assertEqual([r['canonical'] for r in recs], ['squat'])

    def test_non_lift_sessions_untouched(self):
        session = {'id': 3, 'week': 29, 'dow': 4, 'type': 'run',
                   'detail': 'Intervaller 6×800m', 'title': 'Intervaller'}

        garmin_server._enrich_strength_plan([session], user_id=1, history=HISTORY)

        self.assertEqual(session['strength_recommendations'], [])
        self.assertEqual(session['strength_recommendation_text'], '')


if __name__ == '__main__':
    unittest.main()
