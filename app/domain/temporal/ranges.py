from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class TimeRangeSpec(StrEnum):
    """A period a question refers to, named rather than computed.

    The model chooses one of these; the dates are worked out here. Asking a
    model for the actual boundaries of "last week" invites arithmetic that is
    confidently wrong, and a wrong range silently changes the answer to a
    question the user expects to be exact.
    """

    TODAY = "today"
    YESTERDAY = "yesterday"
    THIS_WEEK = "this_week"
    LAST_WEEK = "last_week"
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    LAST_7_DAYS = "last_7_days"
    LAST_30_DAYS = "last_30_days"
    ALL_TIME = "all_time"


@dataclass(frozen=True)
class TimeRange:
    """A half-open interval, [start, end).

    Half-open so that consecutive periods neither overlap nor leave a gap: an
    event at exactly midnight belongs to one day, not to two.
    """

    start: datetime | None
    end: datetime | None
    spec: TimeRangeSpec

    def contains(self, moment: datetime) -> bool:
        if self.start is not None and moment < self.start:
            return False
        return not (self.end is not None and moment >= self.end)

    def is_unbounded(self) -> bool:
        return self.start is None and self.end is None


def resolve_timezone(name: str | None) -> ZoneInfo:
    """Interpret a stored timezone name, falling back to UTC.

    A profile can hold whatever string was put there, and an unknown zone should
    give slightly wrong boundaries rather than a failed request.
    """
    if not name:
        return ZoneInfo("UTC")

    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def resolve_range(
    spec: TimeRangeSpec, now: datetime, timezone: ZoneInfo | None = None
) -> TimeRange:
    """Turn a named period into concrete boundaries.

    Resolved in the user's timezone, because "last week" is a question about
    their calendar, not the server's. A UTC answer can include or exclude a whole
    day's activity for anyone far enough from Greenwich.
    """
    zone = timezone or ZoneInfo("UTC")
    local_now = now.astimezone(zone)
    today = local_now.date()

    if spec is TimeRangeSpec.ALL_TIME:
        return TimeRange(start=None, end=None, spec=spec)

    if spec is TimeRangeSpec.TODAY:
        return _from_dates(today, today + timedelta(days=1), zone, spec)

    if spec is TimeRangeSpec.YESTERDAY:
        return _from_dates(today - timedelta(days=1), today, zone, spec)

    if spec is TimeRangeSpec.THIS_WEEK:
        start = today - timedelta(days=today.weekday())
        return _from_dates(start, start + timedelta(days=7), zone, spec)

    if spec is TimeRangeSpec.LAST_WEEK:
        this_week = today - timedelta(days=today.weekday())
        return _from_dates(this_week - timedelta(days=7), this_week, zone, spec)

    if spec is TimeRangeSpec.THIS_MONTH:
        start = today.replace(day=1)
        return _from_dates(start, _next_month(start), zone, spec)

    if spec is TimeRangeSpec.LAST_MONTH:
        this_month = today.replace(day=1)
        return _from_dates(_previous_month(this_month), this_month, zone, spec)

    if spec is TimeRangeSpec.LAST_7_DAYS:
        return _from_dates(today - timedelta(days=6), today + timedelta(days=1), zone, spec)

    return _from_dates(today - timedelta(days=29), today + timedelta(days=1), zone, spec)


def _from_dates(start: date, end: date, zone: ZoneInfo, spec: TimeRangeSpec) -> TimeRange:
    # Back to UTC for the database; local dates only set the boundaries.
    return TimeRange(
        start=datetime.combine(start, time.min, tzinfo=zone).astimezone(UTC),
        end=datetime.combine(end, time.min, tzinfo=zone).astimezone(UTC),
        spec=spec,
    )


def _next_month(day: date) -> date:
    return (
        day.replace(year=day.year + 1, month=1)
        if day.month == 12
        else day.replace(month=day.month + 1)
    )


def _previous_month(day: date) -> date:
    return (
        day.replace(year=day.year - 1, month=12)
        if day.month == 1
        else day.replace(month=day.month - 1)
    )


def describe(time_range: TimeRange, timezone: ZoneInfo | None = None) -> str:
    """Render a range for a prompt, in terms a person would recognise."""
    if time_range.is_unbounded():
        return "all time"

    zone = timezone or ZoneInfo("UTC")
    if time_range.start is None or time_range.end is None:
        # Not produced by resolve_range, but describe it rather than raise.
        return "an open period"

    start = time_range.start.astimezone(zone).date()
    # The interval is half-open, so the last day included is the day before end.
    last = (time_range.end.astimezone(zone) - timedelta(seconds=1)).date()

    if start == last:
        return start.isoformat()

    return f"{start.isoformat()} to {last.isoformat()}"


def local_midday(days_ago: int, now: datetime, timezone: str | None) -> datetime:
    """Midday, in the user's calendar, a number of days before now.

    Where an event is placed when only its day is known. Midday rather than
    midnight, so that converting to UTC cannot move it into the neighbouring day,
    which would move it between weeks for anyone far enough from Greenwich.
    """
    zone = resolve_timezone(timezone)
    local_day = (now.astimezone(zone) - timedelta(days=days_ago)).date()
    return local_midday_on(local_day, zone)


def local_midday_on(day: date, zone: ZoneInfo) -> datetime:
    """Midday of a calendar day in a zone, as a UTC instant.

    The one place a known local day becomes a stored instant, shared by
    extraction and by a user correcting an event's date, so both place events
    the same way and a corrected event lands where the date filter expects it.
    """
    return datetime.combine(day, time(hour=12), tzinfo=zone).astimezone(UTC)


def local_date(moment: datetime, zone: ZoneInfo) -> date:
    """The calendar day a stored instant falls on for someone in ``zone``."""
    return moment.astimezone(zone).date()


def local_day_bounds(
    first: date | None, last: date | None, zone: ZoneInfo
) -> tuple[datetime | None, datetime | None]:
    """UTC boundaries covering whole local days, ``first`` to ``last`` inclusive.

    Returned half-open, [start, end), like every other range here: the end is
    the midnight that begins the day after ``last``. Either side may be None,
    leaving that side unbounded. Callers keep the dates well inside the
    calendar's limits, since the day after 9999-12-31 does not exist.
    """
    start = (
        datetime.combine(first, time.min, tzinfo=zone).astimezone(UTC)
        if first is not None
        else None
    )
    end = (
        datetime.combine(last + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
        if last is not None
        else None
    )
    return start, end
