import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import Profile


class ProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_user_id(self, user_id: uuid.UUID) -> Profile | None:
        """Load a profile with its collections.

        The relationships use selectin loading, so this is a bounded number of
        queries regardless of how many goals or preferences exist.
        """
        statement = select(Profile).where(Profile.user_id == user_id)
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def timezone_for_user(self, user_id: uuid.UUID) -> str | None:
        """The stored timezone name alone, or None without a profile.

        A single column, for callers that only need to place dates in the
        user's calendar and would otherwise load every goal and preference.
        """
        statement = select(Profile.timezone).where(Profile.user_id == user_id)
        return (await self._session.execute(statement)).scalar_one_or_none()

    def add(self, profile: Profile) -> None:
        self._session.add(profile)
