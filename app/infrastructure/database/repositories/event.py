import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.batching import fetch_all_in_batches
from app.infrastructure.database.models import Event


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def all_for_user(self, user_id: uuid.UUID) -> list[Event]:
        """Every event recorded for the user, in the order they happened."""
        statement = (
            select(Event)
            .where(Event.user_id == user_id)
            .order_by(Event.occurred_at.asc(), Event.id.asc())
        )
        return await fetch_all_in_batches(self._session, statement)

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
