import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import AgentRun


class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, run: AgentRun) -> None:
        self._session.add(run)

    async def get_owned_by(self, run_id: uuid.UUID, user_id: uuid.UUID) -> AgentRun | None:
        """Load a run only if it belongs to this user, in one query."""
        statement = select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user_id)
        return (await self._session.execute(statement)).scalar_one_or_none()
