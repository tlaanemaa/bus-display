"""Host-side tests for the pure UTC->Stockholm conversion in localtime.py.
Reference dates (last Sunday of March/October 2026) cross-checked against
Python's stdlib `calendar` module independently of this module's own
day-of-week implementation.
"""
import localtime


def test_day_of_week_known_date():
    # 2024-01-01 was a Monday.
    assert localtime.day_of_week(2024, 1, 1) == 1


def test_last_sunday_march_2026():
    assert localtime._last_sunday(2026, 3) == 29


def test_last_sunday_october_2026():
    assert localtime._last_sunday(2026, 10) == 25


def test_is_cest_winter():
    assert localtime.is_cest(2026, 1, 15, 12) is False


def test_is_cest_summer():
    assert localtime.is_cest(2026, 7, 9, 12) is True


def test_is_cest_just_before_spring_transition():
    # 2026-03-29 00:59 UTC -- still CET.
    assert localtime.is_cest(2026, 3, 29, 0) is False


def test_is_cest_at_spring_transition():
    # 2026-03-29 01:00 UTC -- CEST begins.
    assert localtime.is_cest(2026, 3, 29, 1) is True


def test_is_cest_just_before_autumn_transition():
    # 2026-10-25 00:59 UTC -- still CEST.
    assert localtime.is_cest(2026, 10, 25, 0) is True


def test_is_cest_at_autumn_transition():
    # 2026-10-25 01:00 UTC -- CET resumes.
    assert localtime.is_cest(2026, 10, 25, 1) is False


def test_add_hours_no_rollover():
    assert localtime.add_hours(2026, 7, 9, 10, 30, 0, 2) == (2026, 7, 9, 12, 30, 0)


def test_add_hours_day_rollover():
    assert localtime.add_hours(2026, 7, 9, 23, 30, 0, 2) == (2026, 7, 10, 1, 30, 0)


def test_add_hours_month_rollover():
    assert localtime.add_hours(2026, 7, 31, 23, 0, 0, 2) == (2026, 8, 1, 1, 0, 0)


def test_add_hours_year_rollover():
    assert localtime.add_hours(2026, 12, 31, 23, 0, 0, 2) == (2027, 1, 1, 1, 0, 0)


def test_utc_to_stockholm_summer_offset():
    # 2026-07-09 13:58 UTC -> 15:58 CEST (+2)
    y, mo, d, h, mi, s, cest = localtime.utc_to_stockholm(2026, 7, 9, 13, 58, 0)
    assert (y, mo, d, h, mi, s) == (2026, 7, 9, 15, 58, 0)
    assert cest is True


def test_utc_to_stockholm_winter_offset():
    # 2026-01-15 22:30 UTC -> 2026-01-15 23:30 CET (+1), no day rollover
    y, mo, d, h, mi, s, cest = localtime.utc_to_stockholm(2026, 1, 15, 22, 30, 0)
    assert (y, mo, d, h, mi, s) == (2026, 1, 15, 23, 30, 0)
    assert cest is False


def test_utc_to_stockholm_winter_day_rollover():
    # 2026-01-15 23:30 UTC -> 2026-01-16 00:30 CET (+1), day rolls over
    y, mo, d, h, mi, s, cest = localtime.utc_to_stockholm(2026, 1, 15, 23, 30, 0)
    assert (y, mo, d, h, mi, s) == (2026, 1, 16, 0, 30, 0)


def test_format_date():
    assert localtime.format_date(2026, 7, 9) == "Thu 9 Jul"


def test_format_time():
    assert localtime.format_time(9, 5) == "09:05"
