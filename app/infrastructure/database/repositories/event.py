from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import Event


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, event: Event) -> None:
        self._session.add(event)
