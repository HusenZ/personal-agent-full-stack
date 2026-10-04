import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import User


def normalise_email(email: str) -> str:
    """Fold an address to the form stored in the unique index."""
    return email.strip().lower()


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        statement = select(User).where(User.email == normalise_email(email))
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        statement = select(User.id).where(User.email == normalise_email(email))
        return (await self._session.execute(statement)).first() is not None

    def add(self, user: User) -> None:
        self._session.add(user)

    async def delete(self, user_id: uuid.UUID) -> None:
        """Delete a user; every row they own goes with them by database cascade."""
        await self._session.execute(delete(User).where(User.id == user_id))
