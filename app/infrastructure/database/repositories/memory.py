import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory.types import MemoryStatus, MemoryType
from app.infrastructure.database.models import Memory, MemoryVersion


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_owned_by(self, memory_id: uuid.UUID, user_id: uuid.UUID) -> Memory | None:
        """Load a memory only if it belongs to this user.

        Ownership is part of the query rather than a check afterwards, so there
        is no path that reads a memory first and authorises it second.
        """
        statement = select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id)
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def active_for_comparison(
        self, user_id: uuid.UUID, limit: int, now: datetime
    ) -> list[Memory]:
        """Memories a new candidate should be compared against.

        Expired memories are excluded: reinforcing or superseding something that
        has already stopped applying would bring it back to life.
        """
        statement = (
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.status == MemoryStatus.ACTIVE,
                (Memory.expires_at.is_(None)) | (Memory.expires_at > now),
            )
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(limit)
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        statuses: tuple[MemoryStatus, ...] | None = None,
        memory_type: MemoryType | None = None,
    ) -> tuple[list[Memory], int]:
        filters = [Memory.user_id == user_id]
        if statuses:
            filters.append(Memory.status.in_(statuses))
        if memory_type is not None:
            filters.append(Memory.memory_type == memory_type)

        total = await self._session.scalar(select(func.count()).select_from(Memory).where(*filters))

        statement = (
            select(Memory)
            .where(*filters)
            .order_by(Memory.created_at.desc(), Memory.id.desc())
            .limit(limit)
            .offset(offset)
        )
        memories = list((await self._session.execute(statement)).scalars().all())

        return memories, int(total or 0)

    def add(self, memory: Memory) -> None:
        self._session.add(memory)

    def add_version(self, version: MemoryVersion) -> None:
        self._session.add(version)

    async def delete(self, memory: Memory) -> None:
        """Remove a memory outright.

        A user deleting something about themselves should have it gone, not
        marked hidden; a tombstone still holding the content is not a deletion.
        Versions cascade with it.
        """
        await self._session.delete(memory)
