"""Plans, what each one allows, and the period usage is counted over.

The limits are a product decision about what each plan is worth, not something
an operator should tune per deployment, so they live here rather than in
configuration. Only features that spend money on a model provider are metered;
onboarding is free, because no one can see what the product is worth before it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from types import MappingProxyType


class Plan(StrEnum):
    FREE = "free"
    PRO = "pro"


class Feature(StrEnum):
    CHAT_MESSAGE = "chat_message"
    WEEK_PLAN = "week_plan"
    ANALYSIS = "analysis"


# Pro is capped so no account costs more than it pays; rate limits still apply.
PLAN_LIMITS: Mapping[Plan, Mapping[Feature, int]] = MappingProxyType(
    {
        Plan.FREE: MappingProxyType(
            {Feature.CHAT_MESSAGE: 50, Feature.WEEK_PLAN: 2, Feature.ANALYSIS: 2}
        ),
        Plan.PRO: MappingProxyType(
            {Feature.CHAT_MESSAGE: 1500, Feature.WEEK_PLAN: 20, Feature.ANALYSIS: 20}
        ),
    }
)


def limit_for(plan: Plan, feature: Feature) -> int:
    return PLAN_LIMITS[plan][feature]


def effective_plan(plan: Plan, expires_at: datetime | None, now: datetime) -> Plan:
    """The plan in force at `now`.

    Expiry is evaluated when read rather than by a job that downgrades
    accounts, so a lapsed plan can never outlive its end date because a job
    did not run.
    """
    if expires_at is not None and expires_at <= now:
        return Plan.FREE
    return plan


@dataclass(frozen=True)
class UsagePeriod:
    starts_on: date
    resets_at: datetime


def usage_period(now: datetime) -> UsagePeriod:
    """The calendar month in UTC that contains `now`.

    UTC rather than the user's zone: changing one's time zone must not be a way
    to start a new month early.
    """
    current = now.astimezone(UTC)
    starts_on = current.date().replace(day=1)
    if starts_on.month == 12:
        next_start = starts_on.replace(year=starts_on.year + 1, month=1)
    else:
        next_start = starts_on.replace(month=starts_on.month + 1)
    return UsagePeriod(
        starts_on=starts_on,
        resets_at=datetime(next_start.year, next_start.month, 1, tzinfo=UTC),
    )
