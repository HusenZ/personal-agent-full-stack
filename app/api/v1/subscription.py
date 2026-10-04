from fastapi import APIRouter

from app.api.dependencies import CurrentUserDep, SubscriptionServiceDep
from app.core.context import get_request_id
from app.schemas.common import DataResponse, ResponseMeta
from app.schemas.subscription import FeatureUsageResponse, SubscriptionResponse

router = APIRouter(prefix="/subscription", tags=["subscription"])


@router.get(
    "",
    response_model=DataResponse[SubscriptionResponse],
    summary="Read your plan and how much of this month's allowance is used",
)
async def read_subscription(
    current_user: CurrentUserDep, service: SubscriptionServiceDep
) -> DataResponse[SubscriptionResponse]:
    view = await service.current(current_user.id)
    return DataResponse(
        data=SubscriptionResponse(
            plan=view.plan,
            expires_at=view.expires_at,
            period_starts_on=view.period_starts_on,
            resets_at=view.resets_at,
            usage=[
                FeatureUsageResponse(
                    feature=item.feature,
                    used=item.used,
                    limit=item.limit,
                    # Usage can exceed the limit after a downgrade; clamp at zero.
                    remaining=max(item.limit - item.used, 0),
                )
                for item in view.usage
            ],
        ),
        meta=ResponseMeta(request_id=get_request_id()),
    )
