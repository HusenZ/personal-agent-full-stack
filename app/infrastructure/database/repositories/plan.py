import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import WeekPlan


class WeekPlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_latest_for_user(self, user_id: uuid.UUID) -> WeekPlan | None:
        statement = (
            select(WeekPlan)
            .where(WeekPlan.user_id == user_id)
            .order_by(WeekPlan.created_at.desc(), WeekPlan.id.desc())
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_owned_by(
        self, plan_id: uuid.UUID, user_id: uuid.UUID, *, for_update: bool = False
    ) -> WeekPlan | None:
        statement = select(WeekPlan).where(WeekPlan.id == plan_id, WeekPlan.user_id == user_id)
        if for_update:
            # Lock so two concurrent ticks don't overwrite each other.
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    def add(self, plan: WeekPlan) -> None:
        self._session.add(plan)
