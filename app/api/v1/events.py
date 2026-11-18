import uuid
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import CurrentUserDep, EventServiceDep
from app.application.events.dto import UNSET, EventFilters, UpdateEventCommand
from app.core.context import get_request_id
from app.domain.events.rules import MAX_ACTIVITY_FILTERS
from app.domain.temporal.ranges import local_date
from app.infrastructure.database.models import Event
from app.schemas.common import DataResponse, ResponseMeta
from app.schemas.event import EventActivity, EventResponse, FilterDate, UpdateEventRequest
from app.schemas.pagination import PageMeta, PageParams, PaginatedResponse, page_params

router = APIRouter(prefix="/events", tags=["events"])

PageParamsDep = Annotated[PageParams, Depends(page_params)]


def to_event_response(event: Event, zone: ZoneInfo) -> EventResponse:
    return EventResponse(
        id=event.id,
        activity=event.activity,
        description=event.description,
        quantity=event.quantity,
        unit=event.unit,
        date=local_date(event.occurred_at, zone),
        occurred_at=event.occurred_at,
        timezone=zone.key,
        memory_id=event.memory_id,
        conversation_id=event.conversation_id,
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


def to_update_command(payload: UpdateEventRequest) -> UpdateEventCommand:
    """Only the fields the client sent, so an explicit null can clear an amount."""
    sent = payload.model_fields_set
    # The schema refuses null for these three, so "sent" always means "has a value";
    # the None checks only narrow the type.
    return UpdateEventCommand(
        activity=(
            payload.activity if "activity" in sent and payload.activity is not None else UNSET
        ),
        description=(
            payload.description
            if "description" in sent and payload.description is not None
            else UNSET
        ),
        on=payload.date if "date" in sent and payload.date is not None else UNSET,
        # These two may be null on purpose: sent as null clears, absent leaves alone.
        quantity=payload.quantity if "quantity" in sent else UNSET,
        unit=payload.unit if "unit" in sent else UNSET,
    )


@router.get(
    "",
    response_model=PaginatedResponse[EventResponse],
    summary="List what you have done, newest first",
    description=(
        "Dates are calendar days in your profile's time zone, and both ends of the "
        "range are included. Repeat `activity` to match any of several."
    ),
)
async def list_events(
    current_user: CurrentUserDep,
    service: EventServiceDep,
    params: PageParamsDep,
    activity: Annotated[
        list[EventActivity] | None,
        Query(
            max_length=MAX_ACTIVITY_FILTERS,
            description="Only these activities, matched exactly after lowercasing.",
        ),
    ] = None,
    start_date: Annotated[
        FilterDate | None, Query(description="First day to include, YYYY-MM-DD.")
    ] = None,
    end_date: Annotated[
        FilterDate | None, Query(description="Last day to include, YYYY-MM-DD.")
    ] = None,
) -> PaginatedResponse[EventResponse]:
    page = await service.list_for_user(
        current_user.id,
        EventFilters(
            # Deduplicated, keeping order; the same label twice means it once.
            activities=tuple(dict.fromkeys(activity or [])),
            start_date=start_date,
            end_date=end_date,
        ),
        limit=params.page_size,
        offset=params.offset,
    )

    return PaginatedResponse(
        data=[to_event_response(event, page.timezone) for event in page.items],
        meta=PageMeta.build(params, page.total_items, get_request_id()),
    )


@router.patch(
    "/{event_id}",
    response_model=DataResponse[EventResponse],
    summary="Correct an event",
)
async def update_event(
    event_id: uuid.UUID,
    payload: UpdateEventRequest,
    current_user: CurrentUserDep,
    service: EventServiceDep,
) -> DataResponse[EventResponse]:
    result = await service.update(event_id, current_user.id, to_update_command(payload))

    return DataResponse(
        data=to_event_response(result.event, result.timezone),
        meta=ResponseMeta(request_id=get_request_id()),
    )


@router.delete(
    "/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete one event permanently",
    description="Only this event is removed; the memory it was recorded with is kept.",
)
async def delete_event(
    event_id: uuid.UUID, current_user: CurrentUserDep, service: EventServiceDep
) -> None:
    await service.delete(event_id, current_user.id)
