import math
import unittest
from datetime import date, timedelta

import plan_calendar
import training_load
import vdot


def activity(day, kind='running', **raw):
    return {'date': day.isoformat(), 'raw': {'activityType': {'typeKey': kind}, **raw}}


class WeeklyIntensityTests(unittest.TestCase):
    def test_zones_running_distance_and_missing_data(self):
        today = date(2027, 1, 5)
        rows = [
            activity(today, hrTimeInZone_1=60, hrTimeInZone_2=120, hrTimeInZone_3=420,
                     hrTimeInZone_4=300, hrTimeInZone_5=300, duration=1200, distance=5000),
            activity(today, 'cycling', hrTimeInZone_2=600, duration=600, distance=20000),
            activity(today, 'trail_running', duration=1800, distance=6000),
            activity(today, 'strength_training', duration=600),
            activity(today + timedelta(days=1), duration=600, distance=2000),
        ]
        weeks = training_load.weekly_intensity(rows, 3, today)
        self.assertEqual([w['week'] for w in weeks], [52, 53, 54])
        self.assertEqual([w['isoWeek'] for w in weeks], [52, 53, 1])
        self.assertFalse(weeks[0]['hasZones'])
        self.assertEqual(weeks[0]['sessions'], 0)
        current = weeks[-1]
        self.assertEqual(current['sessions'], 3)
        self.assertEqual(current['runKm'], 11)
        self.assertEqual(current['totalMin'], 60)
        self.assertEqual((current['lowMin'], current['thresholdMin'], current['highMin']), (20, 5, 5))
        self.assertAlmostEqual(current['lowShare'], 2 / 3, places=4)
        self.assertAlmostEqual(sum(current[k] for k in ('lowShare', 'thresholdShare', 'highShare')), 1, places=3)
        self.assertTrue(current['hasZones'])

    def test_invalid_numbers_and_dates_are_not_nan_or_negative(self):
        today = date(2026, 11, 28)
        rows = [activity(today, hrTimeInZone_1=-20, hrTimeInZone_4='bad',
                         hrTimeInZone_5=float('nan'), duration=None, distance=float('inf')),
                {'date': 'bad', 'raw': {}}, activity(today - timedelta(days=20), duration=200)]
        result = training_load.weekly_intensity(rows, 1, today)[0]
        self.assertEqual(result['totalMin'], 0)
        self.assertEqual(result['runKm'], 0)
        self.assertEqual(result['lowShare'], 0)
        self.assertFalse(result['hasZones'])


class LoadSeriesTests(unittest.TestCase):
    def test_impulse_uses_exponential_decay_and_previous_day_tsb(self):
        today = date(2026, 11, 28)
        rows = [activity(today - timedelta(days=1), activityTrainingLoad=100),
                activity(today - timedelta(days=1), 'cycling', activityTrainingLoad=50)]
        result = training_load.load_series(rows, 3, today)
        series = result['series']
        ctl, atl = 150 * (1 - math.exp(-1 / 42)), 150 * (1 - math.exp(-1 / 7))
        self.assertEqual(series[0]['load'], 0)
        self.assertEqual(series[1]['load'], 150)
        self.assertEqual(series[1]['tsb'], 0)
        self.assertEqual(series[1]['ctl'], round(ctl, 2))
        self.assertEqual(series[1]['atl'], round(atl, 2))
        self.assertEqual(series[2]['tsb'], round(ctl - atl, 2))
        self.assertEqual(series[2]['ctl'], round(ctl * math.exp(-1 / 42), 2))
        self.assertEqual(result['summary']['rampPerWeek'], series[2]['ctl'])

    def test_warmup_and_seven_day_ramp_even_for_one_day_window(self):
        today = date(2026, 11, 28)
        rows = [activity(today - timedelta(days=i), activityTrainingLoad=50) for i in range(91)]
        rows.append(activity(today - timedelta(days=91), activityTrainingLoad=99999))
        result = training_load.load_series(rows, 1, today)
        ctl = 50 * (1 - math.exp(-91 / 42))
        old_ctl = 50 * (1 - math.exp(-84 / 42))
        self.assertEqual(len(result['series']), 1)
        self.assertEqual(result['summary']['ctl'], round(ctl, 2))
        self.assertEqual(result['summary']['rampPerWeek'], round(ctl - old_ctl, 2))

    def test_no_load_is_zero_and_future_load_is_excluded(self):
        today = date(2026, 11, 28)
        rows = [activity(today, activityTrainingLoad=float('nan')),
                activity(today + timedelta(days=1), activityTrainingLoad=900)]
        result = training_load.load_series(rows, 14, today)
        self.assertEqual(len(result['series']), 14)
        self.assertTrue(all(r['ctl'] == r['atl'] == r['tsb'] == r['load'] == 0 for r in result['series']))

    def test_interpretation_boundaries(self):
        for tsb, text in ((-31, 'Hög trötthetsrisk'), (-30, 'Produktiv'),
                          (-10, 'Neutral'), (5, 'Neutral'), (5.1, 'Utvilad')):
            with self.subTest(tsb=tsb):
                self.assertIn(text, training_load._interpretation(tsb, 0))
        for ramp, text in ((8.1, 'Varning'), (8, 'hanterbar'), (3, 'hanterbar'),
                           (-0.1, 'avlastning'), (0, 'Stabil')):
            with self.subTest(ramp=ramp):
                self.assertIn(text, training_load._interpretation(0, ramp))


class RaceEquivalentsTests(unittest.TestCase):
    def test_distances_times_and_paces(self):
        rows = training_load.race_equivalents(50)
        self.assertEqual([r['km'] for r in rows], [1.5, 3, 5, 10, vdot.HALF_KM, vdot.MARATHON_KM])
        for row in rows:
            self.assertEqual(row['seconds'], vdot.race_time(50, row['km']))
            parts = list(map(int, row['text'].split(':')))
            seconds = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
            self.assertEqual(seconds, row['seconds'])
            self.assertEqual(row['paceSec'], round(seconds / row['km']))
        self.assertEqual(len(rows[0]['text'].split(':')), 2)
        self.assertEqual(len(rows[-1]['text'].split(':')), 3)
        self.assertEqual(training_load.race_equivalents(None), [])


if __name__ == '__main__':
    unittest.main()
