from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.temporal.ranges import (
    TimeRange,
    TimeRangeSpec,
    describe,
    local_date,
    local_day_bounds,
    local_midday,
    local_midday_on,
    resolve_range,
    resolve_timezone,
)

# A Saturday, early enough in UTC that several timezones are already on Sunday.
SATURDAY = datetime(2026, 9, 19, 2, 30, tzinfo=UTC)


def bounds(result: TimeRange) -> tuple[datetime, datetime]:
    """Narrow a resolved range to its endpoints.

    Every spec except all_time is bounded, and these tests are about those.
    """
    assert result.start is not None and result.end is not None
    return result.start, result.end


class TestResolveTimezone:
    def test_a_known_zone_is_used(self):
        assert resolve_timezone("Asia/Kolkata").key == "Asia/Kolkata"

    @pytest.mark.parametrize("name", [None, "", "Not/AZone", "nonsense"])
    def test_an_unusable_zone_falls_back_rather_than_raising(self, name: str | None):
        """A profile can hold whatever was put there; slightly wrong boundaries
        beat a failed request."""
        assert resolve_timezone(name).key == "UTC"


class TestDayRanges:
    def test_today_covers_one_day(self):
        start, end = bounds(resolve_range(TimeRangeSpec.TODAY, SATURDAY))

        assert end - start == timedelta(days=1)

    def test_yesterday_ends_where_today_begins(self):
        """Consecutive periods must not overlap or leave a gap."""
        yesterday = resolve_range(TimeRangeSpec.YESTERDAY, SATURDAY)
        today = resolve_range(TimeRangeSpec.TODAY, SATURDAY)

        assert yesterday.end == today.start

    def test_the_moment_itself_falls_inside_today(self):
        assert resolve_range(TimeRangeSpec.TODAY, SATURDAY).contains(SATURDAY)


class TestWeekRanges:
    def test_a_week_is_seven_days(self):
        start, end = bounds(resolve_range(TimeRangeSpec.THIS_WEEK, SATURDAY))

        assert end - start == timedelta(days=7)

    def test_last_week_ends_where_this_week_begins(self):
        last = resolve_range(TimeRangeSpec.LAST_WEEK, SATURDAY)
        this = resolve_range(TimeRangeSpec.THIS_WEEK, SATURDAY)

        assert last.end == this.start

    def test_a_week_starts_on_monday(self):
        start, _ = bounds(resolve_range(TimeRangeSpec.THIS_WEEK, SATURDAY))

        assert start.astimezone(UTC).weekday() == 0

    def test_last_week_does_not_contain_today(self):
        assert not resolve_range(TimeRangeSpec.LAST_WEEK, SATURDAY).contains(SATURDAY)


class TestMonthRanges:
    def test_this_month_starts_on_the_first(self):
        start, _ = bounds(resolve_range(TimeRangeSpec.THIS_MONTH, SATURDAY))

        assert start.astimezone(UTC).day == 1

    def test_last_month_ends_where_this_month_begins(self):
        last = resolve_range(TimeRangeSpec.LAST_MONTH, SATURDAY)
        this = resolve_range(TimeRangeSpec.THIS_MONTH, SATURDAY)

        assert last.end == this.start

    def test_january_rolls_back_to_the_previous_december(self):
        january = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

        start, _ = bounds(resolve_range(TimeRangeSpec.LAST_MONTH, january))

        assert start.astimezone(UTC).year == 2025
        assert start.astimezone(UTC).month == 12

    def test_december_rolls_forward_to_the_next_january(self):
        december = datetime(2026, 12, 15, 12, 0, tzinfo=UTC)

        _, end = bounds(resolve_range(TimeRangeSpec.THIS_MONTH, december))

        assert end.astimezone(UTC).year == 2027
        assert end.astimezone(UTC).month == 1

    def test_a_short_month_is_handled(self):
        """A naive "subtract 30 days" would land in the wrong month here."""
        march = datetime(2026, 3, 31, 12, 0, tzinfo=UTC)

        start, end = bounds(resolve_range(TimeRangeSpec.LAST_MONTH, march))

        assert start.astimezone(UTC).month == 2
        assert end.astimezone(UTC).month == 3


class TestRollingRanges:
    def test_last_seven_days_includes_today(self):
        result = resolve_range(TimeRangeSpec.LAST_7_DAYS, SATURDAY)

        start, end = bounds(result)

        assert result.contains(SATURDAY)
        assert end - start == timedelta(days=7)

    def test_last_thirty_days_includes_today(self):
        result = resolve_range(TimeRangeSpec.LAST_30_DAYS, SATURDAY)

        start, end = bounds(result)

        assert result.contains(SATURDAY)
        assert end - start == timedelta(days=30)


class TestAllTime:
    def test_all_time_is_unbounded(self):
        result = resolve_range(TimeRangeSpec.ALL_TIME, SATURDAY)

        assert result.is_unbounded()

    def test_an_unbounded_range_contains_everything(self):
        result = resolve_range(TimeRangeSpec.ALL_TIME, SATURDAY)

        assert result.contains(datetime(1990, 1, 1, tzinfo=UTC))
        assert result.contains(datetime(2090, 1, 1, tzinfo=UTC))


class TestTimezones:
    def test_the_same_moment_resolves_differently_by_zone(self):
        """This is the whole reason the profile carries a timezone: at 02:30 UTC
        on a Saturday it is already Saturday morning in Kolkata but still Friday
        evening in Los Angeles, so "today" is a different day for each."""
        kolkata = resolve_range(TimeRangeSpec.TODAY, SATURDAY, ZoneInfo("Asia/Kolkata"))
        los_angeles = resolve_range(TimeRangeSpec.TODAY, SATURDAY, ZoneInfo("America/Los_Angeles"))

        assert kolkata.start != los_angeles.start

    def test_a_day_boundary_lands_at_local_midnight(self):
        zone = ZoneInfo("Asia/Kolkata")

        start, _ = bounds(resolve_range(TimeRangeSpec.TODAY, SATURDAY, zone))

        local_start = start.astimezone(zone)
        assert (local_start.hour, local_start.minute) == (0, 0)

    def test_a_week_is_still_seven_days_across_a_clock_change(self):
        """Los Angeles ends daylight saving inside this week; the range must
        still be seven calendar days, not 7 days minus an hour."""
        zone = ZoneInfo("America/Los_Angeles")
        during_change = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

        start, end = bounds(resolve_range(TimeRangeSpec.LAST_WEEK, during_change, zone))

        local_days = (end.astimezone(zone).date() - start.astimezone(zone).date()).days
        assert local_days == 7


class TestDescribe:
    def test_a_single_day_reads_as_one_date(self):
        assert describe(resolve_range(TimeRangeSpec.TODAY, SATURDAY)) == "2026-09-19"

    def test_a_period_reads_as_an_inclusive_span(self):
        """The interval is half-open, so the description must not name the
        exclusive end date as though the user's data covered it."""
        assert describe(resolve_range(TimeRangeSpec.LAST_WEEK, SATURDAY)) == (
            "2026-09-07 to 2026-09-13"
        )

    def test_all_time_is_described_as_such(self):
        assert describe(resolve_range(TimeRangeSpec.ALL_TIME, SATURDAY)) == "all time"

    def test_a_half_open_range_does_not_raise(self):
        result = TimeRange(start=SATURDAY, end=None, spec=TimeRangeSpec.ALL_TIME)

        assert describe(result) == "an open period"


class TestLocalDays:
    def test_bounds_cover_both_days_named_and_end_at_the_next_midnight(self):
        zone = ZoneInfo("Asia/Kolkata")

        start, end = local_day_bounds(date(2026, 3, 10), date(2026, 3, 12), zone)

        assert start is not None and end is not None
        assert start.astimezone(zone) == datetime(2026, 3, 10, tzinfo=zone)
        assert end.astimezone(zone) == datetime(2026, 3, 13, tzinfo=zone)

    def test_an_omitted_side_is_left_open(self):
        zone = ZoneInfo("UTC")

        assert local_day_bounds(None, date(2026, 3, 12), zone)[0] is None
        assert local_day_bounds(date(2026, 3, 10), None, zone)[1] is None
        assert local_day_bounds(None, None, zone) == (None, None)

    def test_a_day_that_loses_an_hour_is_still_one_whole_day(self):
        """New York springs forward on 2026-03-08, so that day is 23 hours long."""
        zone = ZoneInfo("America/New_York")

        start, end = local_day_bounds(date(2026, 3, 8), date(2026, 3, 8), zone)

        assert start is not None and end is not None
        assert end - start == timedelta(hours=23)

    def test_the_calendar_limits_the_filters_allow_do_not_overflow(self):
        for zone in ("Pacific/Kiritimati", "Etc/GMT+12"):
            local_day_bounds(date(1900, 1, 1), date(2999, 12, 31), ZoneInfo(zone))

    @pytest.mark.parametrize(
        "zone", ["UTC", "Asia/Kolkata", "America/Los_Angeles", "Pacific/Kiritimati", "Etc/GMT+12"]
    )
    def test_midday_on_a_day_reads_back_as_that_day(self, zone: str):
        """What makes a corrected date land where the date filter looks for it,
        including where midday is on the other side of the date line in UTC."""
        placed = local_midday_on(date(2026, 11, 17), ZoneInfo(zone))

        assert local_date(placed, ZoneInfo(zone)) == date(2026, 11, 17)
        start, end = local_day_bounds(date(2026, 11, 17), date(2026, 11, 17), ZoneInfo(zone))
        assert start is not None and end is not None
        assert start <= placed < end

    def test_extraction_and_correction_place_a_day_identically(self):
        now = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)
        zone = "Pacific/Auckland"

        extracted = local_midday(2, now, zone)
        two_days_back = local_date(now, ZoneInfo(zone)) - timedelta(days=2)
        corrected = local_midday_on(two_days_back, ZoneInfo(zone))

        assert extracted == corrected
