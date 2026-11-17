import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, Row, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.domain.memory.types import MemoryChange, MemoryStatus, MemoryType
from app.infrastructure.database.batching import fetch_all_in_batches, fetch_rows_in_batches
from app.infrastructure.database.models import Memory, MemoryVersion

# id, memory_id, change, content, status, importance, confidence, reason, created_at
type MemoryVersionRow = Row[
    tuple[
        uuid.UUID,
        uuid.UUID,
        MemoryChange,
        str,
        MemoryStatus,
        float,
        float,
        str | None,
        datetime,
    ]
]


@dataclass(frozen=True)
class StaleEmbedding:
    """A memory semantic search cannot use, and the text to embed for it."""

    id: uuid.UUID
    content: str


def _embedding_is_stale(embedding_model: str) -> ColumnElement[bool]:
    """Semantic search would skip this memory's vector.

    The complement of what search requires: a vector, from the model in use.
    IS DISTINCT FROM rather than != so a row with a vector but no model name
    counts as stale instead of falling through SQL's NULL comparison.
    """
    return or_(Memory.embedding.is_(None), Memory.embedding_model.is_distinct_from(embedding_model))


def _stale_embedding_filters(
    embedding_model: str, *, now: datetime, include_inactive: bool
) -> list[ColumnElement[bool]]:
    """Stale memories that are worth embedding.

    Expired memories are never included: nothing makes an expired memory current
    again, so a vector for one could never be used.
    """
    filters = [
        _embedding_is_stale(embedding_model),
        (Memory.expires_at.is_(None)) | (Memory.expires_at > now),
    ]
    if not include_inactive:
        filters.append(Memory.status == MemoryStatus.ACTIVE)
    return filters


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

    async def all_for_user(self, user_id: uuid.UUID) -> list[Memory]:
        """Every memory of the user in any status, oldest first and unpaginated.

        The vector and search columns are not loaded. Nothing reading every
        memory at once needs them, and parsing a 1536-float vector per row was
        most of the cost of a large account's export. Touching either on a row
        loaded here raises rather than quietly issuing a query per row.
        """
        statement = (
            select(Memory)
            .where(Memory.user_id == user_id)
            .options(
                defer(Memory.embedding, raiseload=True),
                defer(Memory.search_vector, raiseload=True),
            )
            .order_by(Memory.created_at.asc(), Memory.id.asc())
        )
        return await fetch_all_in_batches(self._session, statement)

    async def all_versions_for_user(self, user_id: uuid.UUID) -> list[MemoryVersionRow]:
        """The full change history of every memory the user owns, as plain rows.

        Joined through the memory so ownership is part of the query. Ordered by
        memory and then time, so grouping under each memory is a single pass.
        Rows rather than entities, as there are several per memory.
        """
        statement = (
            select(
                MemoryVersion.id,
                MemoryVersion.memory_id,
                MemoryVersion.change,
                MemoryVersion.content,
                MemoryVersion.status,
                MemoryVersion.importance,
                MemoryVersion.confidence,
                MemoryVersion.reason,
                MemoryVersion.created_at,
            )
            .join(Memory, Memory.id == MemoryVersion.memory_id)
            .where(Memory.user_id == user_id)
            .order_by(
                MemoryVersion.memory_id.asc(),
                MemoryVersion.created_at.asc(),
                MemoryVersion.id.asc(),
            )
        )
        return await fetch_rows_in_batches(self._session, statement)

    async def stale_embeddings(
        self,
        embedding_model: str,
        *,
        after: uuid.UUID | None,
        limit: int,
        now: datetime,
        include_inactive: bool,
    ) -> list[StaleEmbedding]:
        """The next memories, across all users, that need embedding with this model.

        Keyset-paged by id so a caller walking the table sees each row at most
        once per pass, even when it leaves some rows stale on purpose.
        """
        filters = _stale_embedding_filters(
            embedding_model, now=now, include_inactive=include_inactive
        )
        if after is not None:
            filters.append(Memory.id > after)

        statement = (
            select(Memory.id, Memory.content).where(*filters).order_by(Memory.id).limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        return [StaleEmbedding(id=row.id, content=row.content) for row in rows]

    async def count_stale_embeddings(
        self, embedding_model: str, *, now: datetime, include_inactive: bool
    ) -> int:
        filters = _stale_embedding_filters(
            embedding_model, now=now, include_inactive=include_inactive
        )
        total = await self._session.scalar(select(func.count()).select_from(Memory).where(*filters))
        return int(total or 0)

    async def store_embedding(
        self,
        memory: StaleEmbedding,
        embedding: list[float],
        embedding_model: str,
    ) -> bool:
        """Save a vector computed from ``memory.content``, if it still applies.

        Written only while the row still holds that exact text and is still
        stale. A user's edit since the text was read re-embeds or clears the
        vector itself, and must not be overwritten with one for the old words;
        a row someone else has embedded meanwhile is already done. False when
        nothing was written.

        ``updated_at`` is kept: it tells the user when the memory last changed,
        and a new vector for the same words is not a change they made.
        """
        statement = (
            update(Memory)
            .where(
                Memory.id == memory.id,
                Memory.content == memory.content,
                _embedding_is_stale(embedding_model),
            )
            .values(
                embedding=embedding,
                embedding_model=embedding_model,
                updated_at=Memory.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], await self._session.execute(statement))
        return result.rowcount == 1

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
