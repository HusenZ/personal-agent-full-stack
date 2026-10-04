import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import Analysis


class AnalysisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_latest_for_user(self, user_id: uuid.UUID) -> Analysis | None:
        statement = (
            select(Analysis)
            .where(Analysis.user_id == user_id)
            .order_by(Analysis.created_at.desc(), Analysis.id.desc())
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    def add(self, analysis: Analysis) -> None:
        self._session.add(analysis)
