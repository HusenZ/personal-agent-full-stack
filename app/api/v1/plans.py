import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Path, status

from app.api.dependencies import CurrentUserDep, PlanningServiceDep
from app.api.rate_limiting import PlanRateLimit
from app.api.usage_limits import WeekPlanUsage
from app.application.planning.service import WeekPlanView
from app.core.context import get_request_id
from app.domain.planning.week import DAYS_IN_PLAN, PlanBlock
from app.schemas.common import DataResponse, ResponseMeta
from app.schemas.plan import (
    PlanBlockResponse,
    PlanEvidenceResponse,
    UpdatePlanBlockRequest,
    WeekPlanResponse,
)

router = APIRouter(prefix="/plans", tags=["plans"])


def _instants(block: PlanBlock, zone: ZoneInfo) -> tuple[datetime | None, datetime | None]:
    """The block's local wall-clock time, resolved to instants in the plan's zone.

    Clients that write to a device calendar need the instant, and converting an
    arbitrary IANA zone on the client would mean shipping a zone database there.
    """
    if block.start is None:
        return None, None
    hours, minutes = (int(part) for part in block.start.split(":"))
    starts_at = datetime.combine(block.on, time(hours, minutes), tzinfo=zone)
    return starts_at, starts_at + timedelta(minutes=block.duration_minutes)


def _to_response(view: WeekPlanView) -> DataResponse[WeekPlanResponse]:
    zone = ZoneInfo(view.timezone)
    return DataResponse(
        data=WeekPlanResponse(
            id=view.id,
            created_at=view.created_at,
            starts_on=view.starts_on,
            ends_on=view.starts_on + timedelta(days=DAYS_IN_PLAN - 1),
            timezone=view.timezone,
            summary=view.summary,
            blocks=[
                PlanBlockResponse(
                    id=block.id,
                    date=block.on,
                    start=block.start,
                    starts_at=_instants(block, zone)[0],
                    ends_at=_instants(block, zone)[1],
                    duration_minutes=block.duration_minutes,
                    title=block.title,
                    category=block.category,
                    reason=block.reason,
                    evidence_ids=list(block.evidence_ids),
                    done=block.done,
                )
                for block in view.blocks
            ],
            evidence=[
                PlanEvidenceResponse(id=item.id, kind=item.kind, content=item.content)
                for item in view.evidence.items
            ],
        ),
        meta=ResponseMeta(request_id=get_request_id()),
    )


@router.post(
    "/week",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[WeekPlanResponse],
    summary="Plan the next seven days, starting today in your time zone",
)
async def generate_week_plan(
    current_user: CurrentUserDep,
    service: PlanningServiceDep,
    _limit: PlanRateLimit,
    _usage: WeekPlanUsage,
) -> DataResponse[WeekPlanResponse]:
    return _to_response(await service.generate(current_user.id))


@router.get(
    "/week/latest",
    response_model=DataResponse[WeekPlanResponse],
    summary="Read your most recent week plan",
)
async def read_latest_week_plan(
    current_user: CurrentUserDep, service: PlanningServiceDep
) -> DataResponse[WeekPlanResponse]:
    return _to_response(await service.get_latest(current_user.id))


@router.patch(
    "/{plan_id}/blocks/{block_id}",
    response_model=DataResponse[WeekPlanResponse],
    summary="Mark a block done or not done",
)
async def update_plan_block(
    plan_id: uuid.UUID,
    payload: UpdatePlanBlockRequest,
    current_user: CurrentUserDep,
    service: PlanningServiceDep,
    block_id: str = Path(max_length=64),
) -> DataResponse[WeekPlanResponse]:
    return _to_response(
        await service.set_block_done(current_user.id, plan_id, block_id, done=payload.done)
    )
