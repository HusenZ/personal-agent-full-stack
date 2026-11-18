import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.batching import fetch_all_in_batches
from app.infrastructure.database.models import Event


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_owned_by(
        self, event_id: uuid.UUID, user_id: uuid.UUID, *, for_update: bool = False
    ) -> Event | None:
        """Load an event only if it belongs to this user.

        Ownership is part of the query rather than a check afterwards, so there
        is no path that reads an event first and authorises it second.

        ``for_update`` locks the row until the transaction ends. An edit checks
        the new values against the stored ones (an amount needs its unit), and
        without the lock two concurrent edits could each pass that check and
        together leave an incoherent row; a concurrent delete waits instead of
        vanishing underneath the edit.
        """
        statement = select(Event).where(Event.id == event_id, Event.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        activities: tuple[str, ...] = (),
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[Event], int]:
        """A page of the user's events, newest first, and how many match in all.

        ``start`` and ``end`` are a half-open UTC interval, [start, end), as the
        retrievers use. Events placed on the same day share an instant (local
        midday), so ties fall back to when they were recorded and then to id,
        which keeps pages from overlapping or skipping rows.
        """
        filters: list[ColumnElement[bool]] = [Event.user_id == user_id]
        if activities:
            filters.append(Event.activity.in_(activities))
        if start is not None:
            filters.append(Event.occurred_at >= start)
        if end is not None:
            filters.append(Event.occurred_at < end)

        total = await self._session.scalar(select(func.count()).select_from(Event).where(*filters))

        statement = (
            select(Event)
            .where(*filters)
            .order_by(Event.occurred_at.desc(), Event.created_at.desc(), Event.id.desc())
            .limit(limit)
            .offset(offset)
        )
        events = list((await self._session.execute(statement)).scalars().all())

        return events, int(total or 0)

    async def all_for_user(self, user_id: uuid.UUID) -> list[Event]:
        """Every event recorded for the user, in the order they happened."""
        statement = (
            select(Event)
            .where(Event.user_id == user_id)
            .order_by(Event.occurred_at.asc(), Event.id.asc())
        )
        return await fetch_all_in_batches(self._session, statement)

    async def delete_owned_by(self, event_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Delete one event if it belongs to this user. Returns whether it did.

        One statement with ownership in the WHERE clause. The id is the primary
        key, so at most one row can match: nothing else about the user's events,
        or the memory the event was recorded with, is touched.
        """
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                delete(Event).where(Event.id == event_id, Event.user_id == user_id)
            ),
        )
        return result.rowcount == 1

    async def delete_for_memory(self, memory_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Remove the events recorded alongside one of the user's memories.

        An event's description is the memory's text, so leaving events behind
        would keep the words the user asked to have deleted.
        """
        await self._session.execute(
            delete(Event).where(Event.memory_id == memory_id, Event.user_id == user_id)
        )

    def add(self, event: Event) -> None:
        self._session.add(event)
