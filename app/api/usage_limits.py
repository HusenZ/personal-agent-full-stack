"""Plan allowances, applied to the endpoints that spend money on a model.

Declared after the rate limit in each route's signature, so a request the rate
limiter turns away never takes a unit of the user's allowance.
"""

from collections.abc import AsyncIterator, Callable
from typing import Annotated

from fastapi import Depends

from app.api.dependencies import CurrentUserDep, SettingsDep, SubscriptionServiceDep
from app.application.subscription.service import UsageGrant
from app.domain.subscription.plans import Feature

type UsageDependency = Callable[..., AsyncIterator[UsageGrant | None]]


def metered(feature: Feature) -> UsageDependency:
    async def dependency(
        current_user: CurrentUserDep, service: SubscriptionServiceDep, settings: SettingsDep
    ) -> AsyncIterator[UsageGrant | None]:
        if not settings.usage_limits_enabled:
            yield None
            return

        grant = await service.consume(current_user.id, feature)
        try:
            yield grant
        except Exception:
            # Refund on any failure before the response; streams refund themselves.
            await service.refund(grant)
            raise

    return dependency


ChatUsage = Annotated[UsageGrant | None, Depends(metered(Feature.CHAT_MESSAGE))]
WeekPlanUsage = Annotated[UsageGrant | None, Depends(metered(Feature.WEEK_PLAN))]
AnalysisUsage = Annotated[UsageGrant | None, Depends(metered(Feature.ANALYSIS))]
