"""Exercise real replanning through validation and SQL with external services replaced."""
import json
import os
import unittest
from contextlib import ExitStack
from datetime import date, timedelta
from unittest.mock import MagicMock, patch
from werkzeug.security import generate_password_hash

os.environ['APP_TESTING'] = '1'
os.environ['SESSION_SECRET'] = 'test-session-secret-with-at-least-32-characters'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['USERS'] = f'hugo:{generate_password_hash("test-password")}'
os.environ['DATABASE_URL'] = 'postgresql://unused-in-tests'

import garmin_server as server
from plan_changes import InvalidPlanChange, validate_proposal

TODAY = date(2026, 9, 12)

class FixedDate(date):
    @classmethod
    def today(cls):
        return TODAY


def session(sid, offset=0):
    day = TODAY + timedelta(days=offset)
    return {'id': sid, 'week': day.isocalendar().week, 'dow': day.weekday(),
            'type': 'run', 'km': 5, 'title': f'Pass {sid}', 'detail': '5 km lugnt',
            'status': 'planned', 'user_id': 2}


def change(action='modify', sid=1, offset=None, **kwargs):
    result = {'action': action, 'session_id': sid, **kwargs}
    if offset is not None:
        day = TODAY + timedelta(days=offset)
        result.update(new_week=day.isocalendar().week, new_dow=day.weekday())
    return result


def proposal(*changes):
    return {'changes': list(changes), 'coaching_notes': 'Lugn återstart.', 'summary': 'Klart.'}


class ProposalTests(unittest.TestCase):
    def test_invalid_proposal_is_rejected_as_a_whole(self):
        invalid = [None, [], {}, {'changes': None}, {'changes': [None]},
                   proposal(change('delete')), proposal(change(sid=999, new_km=4)),
                   proposal(change(sid=True, new_km=4)),
                   proposal(change(new_km=-1)), proposal(change(new_km=float('nan'))),
                   proposal(change(new_km='5')), proposal(change(type='interval')),
                   proposal(change('reschedule', offset=-1)),
                   proposal(change('reschedule', new_week=37, new_dow=7)),
                   proposal(change('reschedule', new_week=True, new_dow=5)),
                   proposal(change('modify')), proposal(change('add', sid=None, offset=1)),
                   proposal(change(sid=999, new_km=4), change(new_km=6))]
        for result in invalid:
            with self.subTest(result=result), self.assertRaises(InvalidPlanChange):
                validate_proposal(result, [session(1)], TODAY)

    def test_two_changes_to_the_same_session_are_merged_not_rejected(self):
        """En sammansatt begäran ger lätt både en flytt och en omskrivning.

        Avsikten är rätt, bara formen fel — och att kasta hela förslaget tog
        med sig alla andra dagar löparen faktiskt bad om."""
        result = proposal(change('reschedule', offset=2, new_title='Intervaller'),
                          change('modify', new_km=6, reason='Kortare pass.'))
        validate_proposal(result, [session(1)], TODAY)
        merged, = result['changes']
        # Sista ordet gäller, men flytten som redan bestämts får inte tappas.
        self.assertEqual(merged['action'], 'modify')
        self.assertEqual(merged['new_km'], 6)
        self.assertEqual(merged['new_title'], 'Intervaller')
        self.assertEqual(merged['new_dow'], (TODAY + timedelta(days=2)).weekday())

    def test_a_trailing_keep_never_undoes_a_real_change(self):
        result = proposal(change('reschedule', offset=1), change('keep'))
        validate_proposal(result, [session(1)], TODAY)
        merged, = result['changes']
        self.assertEqual(merged['action'], 'reschedule')
        self.assertEqual(merged['new_dow'], (TODAY + timedelta(days=1)).weekday())

    def test_past_date_on_keep_or_skip_does_not_sink_the_whole_proposal(self):
        """keep/skip flyttar ingenting, så ett passerat datum på dem ska ignoreras.

        Modellen fyller ofta i passets nuvarande dag — för ett missat pass ligger
        den bakåt i tiden — och förslaget avvisades då i sin helhet."""
        for action in ('keep', 'skip'):
            with self.subTest(action=action):
                result = proposal(change(action, offset=-3))
                self.assertIs(validate_proposal(result, [session(1)], TODAY), result)
                # Datumet får inte leva vidare och flytta passet nedströms.
                self.assertIsNone(result['changes'][0]['new_week'])
                self.assertIsNone(result['changes'][0]['new_dow'])

    def test_rejection_carries_the_rule_that_failed(self):
        with self.assertRaises(InvalidPlanChange) as caught:
            validate_proposal(proposal(change('reschedule', offset=-1)), [session(1)], TODAY)
        self.assertIn('redan passerat', caught.exception.reason)
        self.assertNotIn('redan passerat', str(caught.exception))

    def test_valid_mixed_proposal(self):
        result = proposal(change('skip'), change('reschedule', 2, 2),
                          change('add', None, 3, type='easy', new_km=4,
                                 new_title='Lugn start', new_detail='4 km lugnt'))
        self.assertIs(validate_proposal(result, [session(1), session(2, 1)], TODAY), result)

    def test_rest_is_bound_to_its_own_day_and_not_negated(self):
        for text, expected in [('Vila idag och springa imorgon', {0}),
                               ('Springa idag och vila imorgon', {1}),
                               ('Jag vill inte vila idag', set()),
                               ('Skippa dagens pass', {0})]:
            with self.subTest(text=text):
                self.assertEqual(server._requested_rest_offsets(text), expected)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        def replace(name, **kwargs):
            return self.stack.enter_context(patch.object(server, name, **kwargs))
        replace('date', new=FixedDate)
        replace('uid', return_value=2)
        replace('uname', return_value='second-user')
        replace('llm_available', return_value=True)
        replace('get_garmin', side_effect=RuntimeError('Garmin offline'))
        replace('_goal_prompt_block', return_value='Goal: regain routine')
        replace('_recent_execution_block', return_value='')
        replace('_strength_progression_history', return_value=[])
        replace('get_cache', return_value=None)
        self.cache = replace('set_cache')
        replace('clear_cache')
        self.llm = replace('call_llm')
        self.conn = MagicMock()
        self.conn.__enter__.return_value = self.conn
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.cur.fetchall.side_effect = [[], [session(1), session(2, 1)], []]
        self.cur.rowcount = 1
        replace('db', return_value=self.conn)

    def run_plan(self, result, request='Anpassa schemat för en lugn återstart'):
        self.llm.return_value = json.dumps(result)
        return server.ai_adjust_plan(request)

    def writes(self):
        return [(c.args[0], c.args[1]) for c in self.cur.execute.call_args_list
                if c.args[0].lstrip().startswith(('UPDATE', 'INSERT'))]

    def test_mixed_changes_persist_for_current_user_without_garmin(self):
        result = self.run_plan(proposal(change('skip'), change('reschedule', 2, 2),
            change('add', None, 3, type='easy', new_km=4, new_title='Återstart', new_detail='4 km lugnt')))
        self.assertEqual(result['changes'], 3)
        self.assertEqual(len(self.writes()), 3)
        for _, params in self.writes():
            self.assertEqual(params[-1], 2)
        self.conn.commit.assert_called_once()
        self.assertEqual(self.llm.call_args.kwargs['json_schema'], server.PLAN_CHANGE_SCHEMA)
        self.assertNotIn('W23-41', self.llm.call_args.args[0])

    def test_a_rejected_proposal_is_sent_back_to_the_coach_once(self):
        """Ett formfel ska kosta ett omförsök, inte hela begäran."""
        self.llm.side_effect = [
            json.dumps(proposal(change('reschedule', 999, 2))),   # hittepå-pass
            json.dumps(proposal(change('reschedule', 1, 2))),
        ]
        result = server.ai_adjust_plan('Flytta passet till torsdag')
        self.assertEqual(result['changes'], 1)
        self.assertEqual(self.llm.call_count, 2)
        # Coachen måste få veta vad som brast, annars upprepar den felet.
        retry_prompt = self.llm.call_args_list[1].args[0]
        self.assertIn('YOUR PREVIOUS ANSWER WAS REJECTED', retry_prompt)
        self.assertIn('999', retry_prompt)

    def test_a_second_rejection_writes_nothing(self):
        self.llm.side_effect = [json.dumps(proposal(change('reschedule', 999, 2)))] * 2
        with self.assertRaises(InvalidPlanChange):
            server.ai_adjust_plan('Flytta passet till torsdag')
        self.assertEqual(self.llm.call_count, 2)
        self.assertEqual(self.writes(), [])
        self.conn.commit.assert_not_called()

    def test_unknown_session_aborts_every_change(self):
        with self.assertRaises(InvalidPlanChange):
            self.run_plan(proposal(change('skip'), change('skip', 999)))
        self.assertEqual(self.writes(), [])
        self.conn.commit.assert_not_called()

    def test_quota_error_is_propagated_without_success_cache(self):
        self.llm.side_effect = server.LLMQuotaError('quota')
        with self.assertRaises(server.LLMQuotaError):
            server.ai_adjust_plan('Ändra schemat')
        self.assertEqual(self.writes(), [])
        self.cache.assert_not_called()

    def test_truncated_json_never_writes(self):
        self.llm.return_value = '{"changes": ['
        with self.assertRaises(InvalidPlanChange):
            server.ai_adjust_plan('Ändra schemat')
        self.assertEqual(self.writes(), [])

    def test_completed_session_race_is_not_counted_or_committed(self):
        self.cur.rowcount = 0
        with self.assertRaises(InvalidPlanChange):
            self.run_plan(proposal(change('reschedule', 1, 2)))
        self.conn.commit.assert_not_called()
        self.cache.assert_not_called()
        self.assertIn("status IN ('planned','missed')", self.writes()[0][0])

    def test_cache_failure_does_not_hide_a_successful_save(self):
        self.cache.side_effect = RuntimeError('cache unavailable')
        result = self.run_plan(proposal(change('skip')))
        self.assertEqual(result['changes'], 1)
        self.conn.commit.assert_called_once()

    def test_rest_today_overrides_keep_without_losing_tomorrow(self):
        result = self.run_plan(proposal(change('keep'), change('modify', 2, new_km=3)),
                               'Ändra planen: vila idag och springa imorgon')
        self.assertEqual(result['changes'], 2)
        self.assertEqual(sum("status='skipped'" in sql for sql, _ in self.writes()), 1)

    def test_rest_tomorrow_keeps_the_rest_of_replanning(self):
        result = self.run_plan(proposal(change('modify', 1, new_km=3), change('keep', 2)),
                               'Ändra planen: springa idag och vila imorgon')
        self.assertEqual(result['changes'], 2)
        self.assertEqual(sum("status='skipped'" in sql for sql, _ in self.writes()), 1)

    def test_empty_plan_can_receive_restart_workouts(self):
        self.cur.fetchall.side_effect = [[], [], []]
        result = self.run_plan(proposal(change('add', None, 2, type='easy', new_km=3,
                                               new_title='Omstart', new_detail='3 km lugnt')))
        self.assertEqual(result['changes'], 1)

    def test_background_mention_of_today_does_not_move_restart_to_today(self):
        self.run_plan(proposal(change('reschedule', 1, 2, new_title='Omstart')),
                      'Idag är lördag, planera pass på måndag')
        _, params = self.writes()[0]
        monday = TODAY + timedelta(days=2)
        self.assertEqual(params[:2], [monday.isocalendar().week, monday.weekday()])

    def test_wrapper_never_reads_old_success_after_failure(self):
        with patch.object(server, 'match_activities_to_plan'), \
             patch.object(server, 'ai_adjust_plan', return_value=None), \
             patch.object(server, 'get_cache', return_value=({'changes': 3}, 0)) as cache:
            with self.assertRaises(InvalidPlanChange):
                server._apply_plan_request('Ändra planen')
            cache.assert_not_called()

    def test_wrapper_continues_when_activity_matching_is_unavailable(self):
        with patch.object(server, 'match_activities_to_plan', side_effect=RuntimeError('offline')), \
             patch.object(server, 'ai_adjust_plan', return_value={'changes': 1}):
            self.assertEqual(server._apply_plan_request('Ändra planen'), {'changes': 1})


if __name__ == '__main__':
    unittest.main()
