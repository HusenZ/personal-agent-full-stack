import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.events.dto import (
    UNSET,
    EventFilters,
    EventPage,
    EventResult,
    UpdateEventCommand,
)
from app.application.events.exceptions import (
    EventAmountIncompleteError,
    EventDateInFutureError,
    EventNotFoundError,
    InvalidDateRangeError,
)
from app.domain.events.rules import amount_is_coherent, is_in_future
from app.domain.temporal.ranges import (
    local_date,
    local_day_bounds,
    local_midday_on,
    resolve_timezone,
)
from app.infrastructure.database.repositories.event import EventRepository
from app.infrastructure.database.repositories.profile import ProfileRepository

logger = logging.getLogger(__name__)


class EventService:
    """Lets a user see and correct the records their totals are computed from.

    Every date crossing this boundary is a calendar day in the user's own time
    zone, read from their profile as the chat's exact totals are. Using the
    same zone in both places is what makes a listed event's date agree with
    the period it is counted in.

    An event is its own record. Editing or deleting one never touches the
    memory it was recorded alongside: a memory can carry several occurrences,
    and correcting one of them says nothing about the others, or about the
    belief the memory holds.
    """

    def __init__(
        self,
        session: AsyncSession,
        events: EventRepository,
        profiles: ProfileRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._events = events
        self._profiles = profiles
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    async def list_for_user(
        self, user_id: uuid.UUID, filters: EventFilters, *, limit: int, offset: int
    ) -> EventPage:
        if (
            filters.start_date is not None
            and filters.end_date is not None
            and filters.start_date > filters.end_date
        ):
            # An empty result would read as "you did nothing", which is not the answer.
            raise InvalidDateRangeError(field="query.end_date")

        zone = await self._zone(user_id)
        start, end = local_day_bounds(filters.start_date, filters.end_date, zone)

        events, total = await self._events.list_for_user(
            user_id,
            limit=limit,
            offset=offset,
            activities=filters.activities,
            start=start,
            end=end,
        )
        return EventPage(items=events, total_items=total, timezone=zone)

    async def update(
        self, event_id: uuid.UUID, user_id: uuid.UUID, command: UpdateEventCommand
    ) -> EventResult:
        """Apply a partial correction to one event.

        Checked against the stored row, under a lock, because a partial update
        can be incoherent only in combination with what is already there:
        clearing the unit of an event that keeps its amount, say.
        """
        zone = await self._zone(user_id)
        event = await self._events.get_owned_by(event_id, user_id, for_update=True)
        if event is None:
            raise EventNotFoundError()

        quantity = event.quantity if command.quantity is UNSET else command.quantity
        unit = event.unit if command.unit is UNSET else command.unit
        touches_amount = command.quantity is not UNSET or command.unit is not UNSET
        # Only an edit to the amount is held to the rule, so a row extraction
        # left incoherent can still have its description or date corrected.
        if touches_amount and not amount_is_coherent(quantity, unit):
            raise EventAmountIncompleteError(field=f"body.{_field_to_fix(command, unit)}")

        if command.on is not UNSET and is_in_future(command.on, local_date(self._clock(), zone)):
            raise EventDateInFutureError(field="body.date")

        # Only what the update names is written; every other column is left exactly as stored.
        if command.activity is not UNSET:
            event.activity = command.activity
        if command.description is not UNSET:
            event.description = command.description
        if command.on is not UNSET:
            # Placed as extraction places events, so the filters find it on that day.
            event.occurred_at = local_midday_on(command.on, zone)
        if command.quantity is not UNSET:
            event.quantity = command.quantity
        if command.unit is not UNSET:
            event.unit = command.unit

        # Read the DB-generated updated_at before committing, while the row lock
        # is still held. Refreshing after the commit raced a delete waiting on
        # that lock: it could land in between, and the refresh then failed with
        # a 500 for an edit that had in fact been saved.
        await self._session.flush()
        await self._session.refresh(event)
        await self._session.commit()

        logger.info(
            "event_updated",
            extra={"user_id": str(user_id), "fields_changed": list(command.changed_fields())},
        )

        return EventResult(event=event, timezone=zone)

    async def delete(self, event_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Remove exactly one event, permanently.

        Totals drop accordingly, which is what deleting a record of an activity
        means. The memory it was recorded with stays; delete that separately.

        Safe against the schema by construction: the event is the child in each
        of its foreign keys and no table references it, so removing the row can
        reach neither its memory nor the other occurrences recorded with it.
        Nothing is committed unless exactly one row went: a missing or foreign
        id leaves the transaction empty, and the request's session rolls it back.
        """
        if not await self._events.delete_owned_by(event_id, user_id):
            raise EventNotFoundError()
        await self._session.commit()

        logger.info("event_deleted", extra={"user_id": str(user_id)})

    async def _zone(self, user_id: uuid.UUID) -> ZoneInfo:
        # Falls back to UTC for a missing or unknown zone, as the chat's totals do.
        return resolve_timezone(await self._profiles.timezone_for_user(user_id))


def _field_to_fix(command: UpdateEventCommand, unit: str | None) -> str:
    """The field a client should send or change to make an amount coherent.

    Sending only one half of an amount is fixed by sending the other half too,
    so that is the one named. When both were sent, the one left empty is.
    """
    if command.unit is UNSET:
        return "unit"
    if command.quantity is UNSET:
        return "quantity"
    return "unit" if unit is None else "quantity"
