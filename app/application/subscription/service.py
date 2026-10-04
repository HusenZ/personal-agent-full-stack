import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.subscription.exceptions import QuotaExceededError
from app.domain.subscription.plans import (
    Feature,
    Plan,
    effective_plan,
    limit_for,
    usage_period,
)
from app.infrastructure.database.repositories.subscription import SubscriptionRepository

logger = logging.getLogger(__name__)

MANUAL_SOURCE = "manual"


@dataclass(frozen=True)
class FeatureUsage:
    feature: Feature
    used: int
    limit: int


@dataclass(frozen=True)
class SubscriptionView:
    plan: Plan
    expires_at: datetime | None
    period_starts_on: date
    resets_at: datetime
    usage: tuple[FeatureUsage, ...]


@dataclass(frozen=True)
class UsageGrant:
    """One unit taken from a user's allowance, so it can be handed back."""

    user_id: uuid.UUID
    feature: Feature
    period_start: date


class SubscriptionService:
    def __init__(
        self,
        session: AsyncSession,
        subscriptions: SubscriptionRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._subscriptions = subscriptions
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    async def current(self, user_id: uuid.UUID) -> SubscriptionView:
        now = self._clock()
        plan, expires_at = await self._plan_at(user_id, now)
        period = usage_period(now)
        used = await self._subscriptions.usage_in_period(user_id, period.starts_on)
        return SubscriptionView(
            plan=plan,
            expires_at=expires_at,
            period_starts_on=period.starts_on,
            resets_at=period.resets_at,
            usage=tuple(
                FeatureUsage(
                    feature=feature, used=used.get(feature, 0), limit=limit_for(plan, feature)
                )
                for feature in Feature
            ),
        )

    async def consume(self, user_id: uuid.UUID, feature: Feature) -> UsageGrant:
        """Take one unit of `feature`, or raise when the plan allows no more.

        Taken before the work rather than counted after it, so concurrent
        requests cannot all pass a check and together overrun the limit. The
        unit is committed at once; a failure afterwards hands it back with
        `refund`.
        """
        now = self._clock()
        plan, _ = await self._plan_at(user_id, now)
        period = usage_period(now)
        limit = limit_for(plan, feature)

        taken = await self._subscriptions.try_consume(user_id, feature, period.starts_on, limit)
        # Commit either way to release the connection before model calls.
        await self._session.commit()

        if not taken:
            logger.info(
                "usage_limit_reached",
                extra={"user_id": str(user_id), "feature": feature.value, "plan": plan.value},
            )
            raise QuotaExceededError(plan, feature, limit, period.resets_at)

        return UsageGrant(user_id=user_id, feature=feature, period_start=period.starts_on)

    async def refund(self, grant: UsageGrant) -> None:
        """Hand back a unit whose request failed, so a failure costs the user nothing.

        Best effort: the caller is already handling a failure, and losing the
        refund costs one unit, while raising here would replace the error that
        matters. Rolls back first because the session may hold the failed
        request's unfinished transaction.
        """
        try:
            await self._session.rollback()
            await self._subscriptions.release(grant.user_id, grant.feature, grant.period_start)
            await self._session.commit()
        except Exception:
            logger.exception(
                "usage_refund_failed",
                extra={"user_id": str(grant.user_id), "feature": grant.feature.value},
            )

    async def grant(
        self,
        user_id: uuid.UUID,
        plan: Plan,
        *,
        expires_at: datetime | None,
        source: str = MANUAL_SOURCE,
    ) -> None:
        await self._subscriptions.put(user_id, plan, source, expires_at)
        await self._session.commit()
        logger.info(
            "subscription_granted",
            extra={"user_id": str(user_id), "plan": plan.value, "source": source},
        )

    async def _plan_at(self, user_id: uuid.UUID, now: datetime) -> tuple[Plan, datetime | None]:
        subscription = await self._subscriptions.get_for_user(user_id)
        if subscription is None:
            return Plan.FREE, None
        plan = effective_plan(subscription.plan, subscription.expires_at, now)
        # An expired grant shows as free with no end date.
        return plan, subscription.expires_at if plan is not Plan.FREE else None
