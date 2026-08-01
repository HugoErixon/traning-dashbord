import unittest

from session_analysis import (
    analyze_run,
    analyze_strength,
    classify_session,
    describe_run,
    format_pace,
    headline_for,
    normalize_laps,
    parse_pace_target,
    parse_rep_target,
    split_work_laps,
)


def lap(distance, duration, hr=None):
    """Build a Garmin-shaped lap; speed is derived so pace maths stays honest."""
    return {'distance': distance, 'duration': duration,
            'averageSpeed': distance / duration, 'averageHR': hr}


class PaceParsingTests(unittest.TestCase):
    def test_reads_a_pace_band_with_en_dash(self):
        target = parse_pace_target('Z2 · 4:50–5:15/km · Lugn och lätt')
        self.assertEqual((target['lowSec'], target['highSec']), (290, 315))

    def test_reads_a_single_pace(self):
        target = parse_pace_target('Tröskel 4:05/km')
        self.assertEqual((target['lowSec'], target['highSec']), (245, 245))

    def test_duration_ranges_are_not_mistaken_for_pace(self):
        self.assertIsNone(parse_pace_target('Z2 · 20–25 min · Spola ur benen'))

    def test_reads_rep_target_with_pace(self):
        target = parse_rep_target('Uppvärmning 2 km · 5×1000m @ 3:30/km · 2 min joggvila')
        self.assertEqual(target['count'], 5)
        self.assertEqual(target['distanceM'], 1000)
        self.assertEqual(target['paceSec'], 210)

    def test_reads_time_based_rep_target(self):
        target = parse_rep_target('Tröskelintervaller · 6×6 min @ 3:50/km · 90 s vila')
        self.assertEqual(target['count'], 6)
        self.assertEqual(target['durationSec'], 360)
        self.assertEqual(target['paceSec'], 230)
        self.assertIsNone(target['distanceM'])

    def test_formats_pace_back_to_text(self):
        self.assertEqual(format_pace(305), '5:05/km')


class ClassificationTests(unittest.TestCase):
    def test_easy_session_is_recognised(self):
        self.assertEqual(
            classify_session({'type': 'easy', 'title': 'Lätt Z2 · 7 km', 'detail': 'Z2'}),
            'easy')

    def test_interval_session_is_recognised(self):
        self.assertEqual(
            classify_session({'type': 'run', 'title': '5×1000m intervaller', 'detail': '@ 3:30/km'}),
            'interval')


class LapTests(unittest.TestCase):
    def test_track_reps_are_separated_from_rest_jogs(self):
        laps = normalize_laps({'lapDTOs': [
            lap(400, 76), lap(200, 120), lap(400, 77), lap(200, 122),
            lap(400, 78), lap(200, 121), lap(400, 79), lap(200, 123),
        ]})
        work = split_work_laps(laps)
        self.assertEqual(len(work), 4)
        self.assertTrue(all(400 <= item['distanceM'] <= 550 for item in work))

    def test_sub_50m_laps_are_dropped_as_noise(self):
        laps = normalize_laps({'lapDTOs': [lap(400, 80), lap(12, 9)]})
        self.assertEqual(len(laps), 1)


class RunAnalysisTests(unittest.TestCase):
    def test_easy_run_done_too_fast_is_flagged(self):
        planned = {'type': 'easy', 'km': 7, 'title': 'Lätt Z2',
                   'detail': 'Z2 · 5:00–5:20/km · Aktiv återhämtning'}
        # 7 km in 31:30 = 4:30/km, a full 30 s/km faster than the easy band.
        activity = {'distance': 7000, 'duration': 1890, 'averageHR': 158}
        result = analyze_run(activity, [], planned)

        self.assertEqual(result['kind'], 'easy')
        self.assertEqual(result['paceVerdict'], 'too_fast')
        self.assertLess(result['paceDeltaPct'], 0)
        self.assertIn('easy_run_too_fast', result['flags'])
        self.assertEqual(headline_for(result), 'Lätt pass för snabbt')

    def test_easy_run_inside_the_band_is_on_target(self):
        planned = {'type': 'easy', 'km': 7, 'detail': 'Z2 · 5:00–5:20/km'}
        activity = {'distance': 7000, 'duration': 7 * 310}  # 5:10/km
        result = analyze_run(activity, [], planned)

        self.assertEqual(result['paceVerdict'], 'on_target')
        self.assertEqual(result['flags'], [])

    def test_easy_run_at_threshold_heart_rate_is_flagged(self):
        planned = {'type': 'easy', 'km': 7, 'detail': 'Z2 · 5:00–5:20/km'}
        activity = {'distance': 7000, 'duration': 7 * 310, 'averageHR': 172}
        result = analyze_run(activity, [], planned, lactate_hr=175)

        self.assertIn('easy_run_hr_too_high', result['flags'])

    def test_intervals_slower_than_target_are_flagged_from_reps(self):
        planned = {'type': 'run', 'km': 9, 'title': '5×1000m intervaller',
                   'detail': 'Uppvärmning 2 km · 5×1000m @ 3:30/km · 2 min joggvila'}
        # Reps average 3:45/km — clearly off the 3:30 target.
        laps = normalize_laps({'lapDTOs': [
            lap(1000, 225, 168), lap(400, 160, 140),
            lap(1000, 225, 170), lap(400, 162, 141),
            lap(1000, 225, 172),
        ]})
        activity = {'distance': 9000, 'duration': 3000, 'averageHR': 160,
                    'activityName': 'Intervaller'}
        result = analyze_run(activity, laps, planned)

        self.assertEqual(result['kind'], 'interval')
        self.assertEqual(len(result['reps']), 3)
        self.assertEqual(result['repVerdict'], 'too_slow')
        self.assertIn('reps_below_target_pace', result['flags'])
        self.assertIn('fewer_reps_than_planned', result['flags'])

    def test_fade_across_reps_is_detected(self):
        planned = {'type': 'run', 'title': 'Intervaller', 'detail': '5×1000m @ 3:30/km'}
        laps = normalize_laps({'lapDTOs': [
            lap(1000, 208), lap(1000, 210), lap(1000, 214), lap(1000, 224),
        ]})
        result = analyze_run({'distance': 9000, 'duration': 3000}, laps, planned)

        self.assertGreater(result['fadePct'], 3)
        self.assertIn('faded_across_reps', result['flags'])

    def test_time_based_intervals_are_judged_against_the_session_pace(self):
        # "6×6 min" states no distance, so the session's own target pace is
        # what the reps were meant to hold.
        planned = {'type': 'run', 'km': 12, 'title': 'Tröskelintervaller · 6×6 min',
                   'detail': '6×6 min @ 3:50/km · 90 s vila'}
        # 1500 m in 6:02 is 4:01/km — a clear 5% off the 3:50/km target.
        laps = normalize_laps({'lapDTOs': [lap(1500, 362), lap(1500, 365), lap(1500, 368)]})
        result = analyze_run({'distance': 12000, 'duration': 4200}, laps, planned)

        self.assertEqual(result['kind'], 'interval')
        self.assertEqual(result['repVerdict'], 'too_slow')
        self.assertIn('reps_below_target_pace', result['flags'])

    def test_easy_run_taken_slower_is_not_treated_as_a_miss(self):
        planned = {'type': 'easy', 'km': 8, 'title': 'Lugn distans',
                   'detail': 'Z2 · 4:50–5:05/km'}
        activity = {'distance': 8000, 'duration': 8 * 377}  # 6:17/km
        result = analyze_run(activity, [], planned)

        self.assertEqual(result['paceVerdict'], 'too_slow')
        self.assertIn('easy_run_slower_than_target', result['flags'])
        self.assertNotIn('slower_than_target', result['flags'])
        self.assertEqual(headline_for(result), 'Lugnare än måltempo')

    def test_session_cut_short_is_flagged(self):
        planned = {'type': 'easy', 'km': 10, 'detail': 'Z2 · 5:00–5:20/km'}
        activity = {'distance': 6000, 'duration': 6 * 310}
        result = analyze_run(activity, [], planned)

        self.assertEqual(result['distanceVerdict'], 'short')
        self.assertIn('cut_session_short', result['flags'])

    def test_prompt_description_includes_reps_and_target(self):
        planned = {'type': 'run', 'title': 'Intervaller', 'detail': '5×1000m @ 3:30/km'}
        laps = normalize_laps({'lapDTOs': [lap(1000, 225), lap(1000, 226)]})
        text = describe_run(analyze_run({'distance': 9000, 'duration': 3000}, laps, planned))

        self.assertIn('work reps', text)
        self.assertIn('rep target', text)
        self.assertIn('too_slow', text)


class StrengthAnalysisTests(unittest.TestCase):
    def test_lifting_below_the_calculated_target_is_flagged(self):
        logged = [{'exercise': 'Knäböj', 'sets': 4, 'reps': '8', 'weight': 70}]
        recommendations = [{'canonical': 'squat', 'exercise': 'Knäböj',
                            'sets': 4, 'reps': 8, 'weight': 85}]
        result = analyze_strength(logged, recommendations)

        self.assertEqual(result['exercises'][0]['verdict'], 'too_light')
        self.assertIn('lifted_light', result['flags'])
        self.assertEqual(headline_for(result), 'Lyfte lättare än målvikt')

    def test_matching_the_target_reads_as_on_target(self):
        logged = [{'exercise': 'Bänkpress', 'sets': 4, 'reps': '8', 'weight': 72.5}]
        recommendations = [{'canonical': 'bench_press', 'exercise': 'Bänkpress',
                            'sets': 4, 'reps': 8, 'weight': 72.5}]
        result = analyze_strength(logged, recommendations)

        self.assertEqual(result['exercises'][0]['verdict'], 'on_target')
        self.assertIn('strength_on_target', result['flags'])

    def test_missing_sets_is_flagged(self):
        logged = [{'exercise': 'Marklyft', 'sets': 2, 'reps': '5', 'weight': 120}]
        recommendations = [{'canonical': 'deadlift', 'exercise': 'Marklyft',
                            'sets': 4, 'reps': 5, 'weight': 120}]
        result = analyze_strength(logged, recommendations)

        self.assertIn('missed_sets', result['flags'])

    def test_on_target_is_not_claimed_when_sets_were_missed(self):
        # The weight was right but the session was cut — reporting both
        # "missed sets" and "on target" reads as contradictory.
        logged = [{'exercise': 'Marklyft', 'sets': 2, 'reps': '5', 'weight': 120}]
        recommendations = [{'canonical': 'deadlift', 'exercise': 'Marklyft',
                            'sets': 4, 'reps': 5, 'weight': 120}]
        result = analyze_strength(logged, recommendations)

        self.assertNotIn('strength_on_target', result['flags'])
        self.assertEqual(headline_for(result), 'Färre set än planerat')


if __name__ == '__main__':
    unittest.main()
