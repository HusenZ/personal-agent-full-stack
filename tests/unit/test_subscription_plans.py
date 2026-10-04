from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.domain.subscription.plans import (
    PLAN_LIMITS,
    Feature,
    Plan,
    effective_plan,
    limit_for,
    usage_period,
)

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


class TestLimits:
    @pytest.mark.parametrize("plan", list(Plan))
    def test_every_plan_limits_every_feature_to_at_least_one(self, plan: Plan):
        # The atomic consume relies on this: a first use is an unguarded insert.
        assert set(PLAN_LIMITS[plan]) == set(Feature)
        assert all(limit >= 1 for limit in PLAN_LIMITS[plan].values())

    @pytest.mark.parametrize("feature", list(Feature))
    def test_pro_never_allows_less_than_free(self, feature: Feature):
        assert limit_for(Plan.PRO, feature) > limit_for(Plan.FREE, feature)


class TestEffectivePlan:
    def test_a_grant_without_an_end_stays_in_force(self):
        assert effective_plan(Plan.PRO, None, NOW) is Plan.PRO

    def test_a_grant_is_in_force_until_it_ends(self):
        assert effective_plan(Plan.PRO, NOW + timedelta(seconds=1), NOW) is Plan.PRO

    @pytest.mark.parametrize("ended", [NOW, NOW - timedelta(days=3)])
    def test_an_ended_grant_falls_back_to_free(self, ended: datetime):
        assert effective_plan(Plan.PRO, ended, NOW) is Plan.FREE


class TestUsagePeriod:
    def test_the_period_is_the_calendar_month(self):
        period = usage_period(NOW)

        assert period.starts_on == date(2026, 10, 1)
        assert period.resets_at == datetime(2026, 11, 1, tzinfo=UTC)

    def test_december_resets_into_the_next_year(self):
        period = usage_period(datetime(2026, 12, 31, 23, 59, tzinfo=UTC))

        assert period.starts_on == date(2026, 12, 1)
        assert period.resets_at == datetime(2027, 1, 1, tzinfo=UTC)

    def test_the_month_is_decided_in_utc_not_the_callers_zone(self):
        # Already 1 November in India, still 31 October in UTC.
        india = timezone(timedelta(hours=5, minutes=30))

        period = usage_period(datetime(2026, 11, 1, 1, 0, tzinfo=india))

        assert period.starts_on == date(2026, 10, 1)
