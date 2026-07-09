"""Pure UTC -> Stockholm local time conversion (CET/CEST, EU DST rule) and
formatting. No time/machine imports -- takes a UTC calendar tuple in,
returns a local one out, so it runs under host CPython with pytest (see
CLAUDE.md "Testability rule"). The device only has UTC from NTP; SL's own
`display` field sidesteps this for departure countdowns, but showing
actual current time / last-updated time needs real local time, hence
this module.
"""

_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_DOW_TABLE = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)
_WEEKDAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _is_leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_in_month(year, month):
    if month == 2 and _is_leap(year):
        return 29
    return _DAYS_IN_MONTH[month - 1]


def day_of_week(year, month, day):
    """0=Sunday..6=Saturday, via Sakamoto's algorithm."""
    y = year - 1 if month < 3 else year
    return (y + y // 4 - y // 100 + y // 400 + _DOW_TABLE[month - 1] + day) % 7


def _last_sunday(year, month):
    day = _days_in_month(year, month)
    while day_of_week(year, month, day) != 0:
        day -= 1
    return day


def is_cest(year, month, day, hour):
    """EU summer-time rule: CEST from the last Sunday in March 01:00 UTC to
    the last Sunday in October 01:00 UTC; CET (winter) otherwise."""
    if month < 3 or month > 10:
        return False
    if 3 < month < 10:
        return True
    if month == 3:
        last_sun = _last_sunday(year, 3)
        return (day > last_sun) or (day == last_sun and hour >= 1)
    last_sun = _last_sunday(year, 10)
    return (day < last_sun) or (day == last_sun and hour < 1)


def add_hours(year, month, day, hour, minute, second, hours):
    """Add `hours` to a naive UTC calendar datetime, handling day/month/
    year rollover. Deliberately no time.mktime/datetime -- must run on
    MicroPython too, which has neither."""
    hour += hours
    while hour >= 24:
        hour -= 24
        day += 1
        if day > _days_in_month(year, month):
            day = 1
            month += 1
            if month > 12:
                month = 1
                year += 1
    while hour < 0:
        hour += 24
        day -= 1
        if day < 1:
            month -= 1
            if month < 1:
                month = 12
                year -= 1
            day = _days_in_month(year, month)
    return year, month, day, hour, minute, second


def utc_to_stockholm(year, month, day, hour, minute, second):
    """Returns (year, month, day, hour, minute, second, is_cest)."""
    cest = is_cest(year, month, day, hour)
    y, mo, d, h, mi, s = add_hours(year, month, day, hour, minute, second, 2 if cest else 1)
    return y, mo, d, h, mi, s, cest


def format_date(year, month, day):
    """e.g. 'Wed 9 Jul'."""
    dow = _WEEKDAY_NAMES[day_of_week(year, month, day)]
    return "%s %d %s" % (dow, day, _MONTH_NAMES[month - 1])


def format_time(hour, minute):
    return "%02d:%02d" % (hour, minute)
