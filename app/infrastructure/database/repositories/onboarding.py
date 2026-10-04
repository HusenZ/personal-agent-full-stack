import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import OnboardingSession, OnboardingStatus


class OnboardingSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_for_user(self, user_id: uuid.UUID) -> OnboardingSession | None:
        statement = (
            select(OnboardingSession)
            .where(
                OnboardingSession.user_id == user_id,
                OnboardingSession.status == OnboardingStatus.IN_PROGRESS,
            )
            # Ordered so the read is deterministic if a race ever creates two.
            .order_by(OnboardingSession.created_at.desc())
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_latest_for_user(self, user_id: uuid.UUID) -> OnboardingSession | None:
        statement = (
            select(OnboardingSession)
            .where(OnboardingSession.user_id == user_id)
            .order_by(OnboardingSession.created_at.desc())
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    def add(self, onboarding_session: OnboardingSession) -> None:
        self._session.add(onboarding_session)
