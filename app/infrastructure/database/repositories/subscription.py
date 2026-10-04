import uuid
from datetime import date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.subscription.plans import Feature, Plan
from app.infrastructure.database.models import Subscription, UsageCounter


class SubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_user(self, user_id: uuid.UUID) -> Subscription | None:
        return await self._session.get(Subscription, user_id)

    async def put(
        self, user_id: uuid.UUID, plan: Plan, source: str, expires_at: datetime | None
    ) -> None:
        statement = insert(Subscription).values(
            user_id=user_id, plan=plan, source=source, expires_at=expires_at
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[Subscription.user_id],
                set_={
                    "plan": statement.excluded.plan,
                    "source": statement.excluded.source,
                    "expires_at": statement.excluded.expires_at,
                    "updated_at": func.now(),
                },
            )
        )

    async def try_consume(
        self, user_id: uuid.UUID, feature: Feature, period_start: date, limit: int
    ) -> bool:
        """Take one unit if fewer than `limit` are used. True when one was taken.

        One conditional upsert, so two requests racing for the last unit cannot
        both get it: the row lock taken by the conflict makes the second see the
        first's increment.
        """
        insert_counter = insert(UsageCounter).values(
            user_id=user_id, feature=feature, period_start=period_start, used=1
        )
        statement = insert_counter.on_conflict_do_update(
            index_elements=[UsageCounter.user_id, UsageCounter.feature, UsageCounter.period_start],
            set_={"used": UsageCounter.used + 1},
            where=UsageCounter.used < limit,
        ).returning(UsageCounter.used)
        # The first use is an insert; every limit is at least one.
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def release(self, user_id: uuid.UUID, feature: Feature, period_start: date) -> None:
        await self._session.execute(
            update(UsageCounter)
            .where(
                UsageCounter.user_id == user_id,
                UsageCounter.feature == feature,
                UsageCounter.period_start == period_start,
                UsageCounter.used > 0,
            )
            .values(used=UsageCounter.used - 1)
        )

    async def usage_in_period(self, user_id: uuid.UUID, period_start: date) -> dict[Feature, int]:
        rows = await self._session.execute(
            select(UsageCounter.feature, UsageCounter.used).where(
                UsageCounter.user_id == user_id, UsageCounter.period_start == period_start
            )
        )
        return {feature: used for feature, used in rows.tuples()}
