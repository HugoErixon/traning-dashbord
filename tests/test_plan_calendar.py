import unittest
from datetime import date, timedelta

import plan_calendar


class PlanCalendarTests(unittest.TestCase):
    def test_2026_plan_weeks_are_iso_weeks(self):
        # Alla rader som sparats under 2026 ska fortsätta betyda samma dag.
        d = date(2026, 1, 1)
        while d.year == 2026:
            iso = d.isocalendar()
            if iso[0] == 2026:
                self.assertEqual(plan_calendar.plan_week_dow(d), (iso[1], iso[2] - 1))
            d += timedelta(days=1)

    def test_weeks_keep_counting_after_new_year(self):
        self.assertEqual(plan_calendar.plan_week(date(2026, 12, 31)), 53)
        self.assertEqual(plan_calendar.plan_week(date(2027, 1, 4)), 54)
        self.assertEqual(plan_calendar.plan_week(date(2027, 3, 7)), 62)
        self.assertEqual(plan_calendar.iso_week_label(62), 9)

    def test_round_trip(self):
        d = date(2026, 10, 1)
        for _ in range(400):
            week, dow = plan_calendar.plan_week_dow(d)
            self.assertEqual(plan_calendar.session_date(week, dow), d)
            d += timedelta(days=1)


if __name__ == '__main__':
    unittest.main()
