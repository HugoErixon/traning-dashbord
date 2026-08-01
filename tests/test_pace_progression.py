import unittest

from pace_progression import (
    derive_anchor,
    describe_anchor,
    goal_feasibility,
    parse_goal_pace,
    target_band,
    validate_proposal,
)
from session_analysis import replace_pace


# Hugo's real numbers on 2026-08-01: Garmin reports a 4:00/km threshold.
LT = 240


class AnchorTests(unittest.TestCase):
    def test_garmin_threshold_is_used_when_nothing_else_is_known(self):
        anchor = derive_anchor(lt_pace_sec=LT)
        self.assertEqual(anchor['ltPaceSec'], 240)
        self.assertEqual(anchor['source'], 'garmin')

    def test_threshold_reps_provide_a_second_estimate(self):
        # Four reps averaging 3:51/km in a threshold session imply a
        # threshold right around that pace.
        executions = [{'kind': 'threshold', 'reps': [
            {'paceSec': 228}, {'paceSec': 232}, {'paceSec': 233}, {'paceSec': 230}]}]
        anchor = derive_anchor(lt_pace_sec=None, executions=executions)
        self.assertEqual(anchor['source'], 'measured')
        self.assertEqual(anchor['ltPaceSec'], 231)

    def test_interval_reps_are_shifted_back_towards_threshold(self):
        # Interval reps are run faster than threshold, so they must not be
        # read as a threshold estimate directly.
        executions = [{'kind': 'interval', 'reps': [
            {'paceSec': 210}, {'paceSec': 212}]}]
        anchor = derive_anchor(executions=executions)
        self.assertEqual(anchor['ltPaceSec'], 225)

    def test_the_slower_of_the_two_anchors_wins(self):
        executions = [{'kind': 'threshold', 'reps': [
            {'paceSec': 220}, {'paceSec': 222}]}]  # implies a 3:41 threshold
        anchor = derive_anchor(lt_pace_sec=LT, executions=executions)

        self.assertEqual(anchor['ltPaceSec'], 240)  # Garmin's slower value kept
        self.assertEqual(anchor['source'], 'garmin+measured')
        self.assertEqual(anchor['confidence'], 'high')

    def test_a_single_rep_is_not_enough_evidence(self):
        executions = [{'kind': 'threshold', 'reps': [{'paceSec': 220}]}]
        anchor = derive_anchor(executions=executions)
        self.assertIsNone(anchor['ltPaceSec'])

    def test_implausible_values_are_ignored(self):
        self.assertIsNone(derive_anchor(lt_pace_sec=24)['ltPaceSec'])


class BandTests(unittest.TestCase):
    def test_threshold_band_sits_at_the_anchor(self):
        band = target_band('threshold', LT)
        self.assertEqual((band['lowSec'], band['highSec']), (240, 248))

    def test_easy_band_is_a_minute_slower_than_threshold(self):
        band = target_band('easy', LT)
        self.assertEqual((band['lowSec'], band['highSec']), (300, 335))

    def test_interval_band_is_faster_than_threshold(self):
        band = target_band('interval', LT)
        self.assertEqual((band['lowSec'], band['highSec']), (220, 232))


class ValidationTests(unittest.TestCase):
    def test_a_pace_inside_the_band_is_accepted(self):
        result = validate_proposal('threshold', 244, LT)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['paceSec'], 244)

    def test_the_plans_actual_threshold_target_is_pulled_back(self):
        # The seeded plan asks for 3:50/km threshold work while the measured
        # threshold is 4:00/km — exactly the session that blew up at 4 of 6.
        result = validate_proposal('threshold', 230, LT)
        self.assertEqual(result['status'], 'clamped')
        self.assertEqual(result['paceSec'], 240)
        self.assertIn('threshold band', result['reason'])

    def test_a_wildly_fast_proposal_is_rejected(self):
        result = validate_proposal('easy', 200, LT)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['paceSec'], 300)

    def test_without_an_anchor_nothing_can_be_validated(self):
        result = validate_proposal('threshold', 240, None)
        self.assertEqual(result['status'], 'rejected')
        self.assertIsNone(result['paceSec'])


class GoalTests(unittest.TestCase):
    def test_sub_1_20_half_is_out_of_reach_on_a_4_00_threshold(self):
        # 1:19:59 over 21.1 km is 3:47/km — faster than threshold, which a
        # half marathon can never be.
        goal = goal_feasibility(227, LT)
        self.assertEqual(goal['verdict'], 'out_of_reach')
        self.assertEqual(goal['currentCapablePace'], '4:08/km')
        self.assertGreater(goal['gapSec'], 10)

    def test_a_goal_just_off_current_capability_reads_as_a_stretch(self):
        goal = goal_feasibility(244, LT)
        self.assertEqual(goal['verdict'], 'stretch')

    def test_a_conservative_goal_is_within_reach(self):
        goal = goal_feasibility(260, LT)
        self.assertEqual(goal['verdict'], 'within_reach')


class GoalParsingTests(unittest.TestCase):
    def test_half_marathon_time_is_read_as_hours_and_minutes(self):
        goal = parse_goal_pace('Halvmara sub 1:20')
        self.assertEqual(goal['timeSec'], 4800)
        self.assertEqual(goal['pace'], '3:48/km')

    def test_short_race_time_is_read_as_minutes_and_seconds(self):
        # The same "10:00" that would be nonsense as hours over 3 km.
        goal = parse_goal_pace('3 km under 10:00')
        self.assertEqual(goal['timeSec'], 600)
        self.assertEqual(goal['pace'], '3:20/km')

    def test_swedish_race_names_are_recognised(self):
        self.assertEqual(parse_goal_pace('Milen under 40 min')['pace'], '4:00/km')
        self.assertEqual(parse_goal_pace('Maraton 3:30:00')['distanceKm'], 42.195)

    def test_a_goal_without_a_race_time_has_no_pace(self):
        self.assertIsNone(parse_goal_pace('Bli starkare och hålla mig skadefri'))


class RewriteTests(unittest.TestCase):
    def test_a_range_stays_a_range(self):
        text = replace_pace('Z2 · 4:50–5:15/km · Lugn och lätt', 305, 340)
        self.assertIn('5:05/km–5:40/km', text)
        self.assertIn('Lugn och lätt', text)

    def test_a_single_pace_stays_single(self):
        text = replace_pace('5×1000m @ 3:30/km · 2 min joggvila', 245)
        self.assertEqual(text, '5×1000m @ 4:05/km · 2 min joggvila')

    def test_text_without_a_pace_gets_one_appended(self):
        text = replace_pace('Lugn distans 8 km', 305)
        self.assertEqual(text, 'Lugn distans 8 km · 5:05/km')


class PromptTests(unittest.TestCase):
    def test_prompt_lists_every_band_and_the_goal_gap(self):
        anchor = derive_anchor(lt_pace_sec=LT)
        text = describe_anchor(anchor, goal_feasibility(227, LT))

        self.assertIn('threshold: 4:00/km–4:08/km', text)
        self.assertIn('easy: 5:00/km–5:35/km', text)
        self.assertIn('out_of_reach', text)

    def test_prompt_refuses_to_invite_paces_without_an_anchor(self):
        self.assertIn('do not propose', describe_anchor(derive_anchor()))


if __name__ == '__main__':
    unittest.main()
