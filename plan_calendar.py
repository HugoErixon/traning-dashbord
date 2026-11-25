"""Plan weeks that keep counting across New Year.

`plan_sessions` stores a week number and a weekday, no year. As long as the
week was the ISO week of the current year, a plan could never cross New Year:
week 2 of next year looked like week 2 of this year. A plan week counts weeks
from ISO week 1 of 2026 instead, so for 2026 it *is* the ISO week (every row
already stored stays valid) and week 1 of 2027 simply becomes week 54.

Show `iso_week_label()` to people; compare and store plan weeks.
"""

from datetime import date, timedelta

# Monday of ISO week 1 2026.
PLAN_EPOCH = date(2025, 12, 29)


def plan_week(d):
    monday = d - timedelta(days=d.weekday())
    return (monday - PLAN_EPOCH).days // 7 + 1


def plan_week_dow(d):
    return plan_week(d), d.weekday()


def plan_monday(week):
    return PLAN_EPOCH + timedelta(days=7 * (int(week) - 1))


def session_date(week, dow):
    return plan_monday(week) + timedelta(days=int(dow))


def iso_week_label(week):
    """The calendar week number a person expects to see (1–53)."""
    return plan_monday(week).isocalendar()[1]
