import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.database.models import Memory
from app.retrieval.filters import usable_memories
from app.retrieval.types import MemoryHit


class SemanticMemoryRetriever:
    """Finds a user's memories by meaning rather than by words."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        # Own short session per search, so no connection is held during model calls.
        self._sessions = sessions

    async def search(
        self,
        user_id: uuid.UUID,
        query_embedding: list[float],
        *,
        embedding_model: str,
        limit: int,
        min_similarity: float,
        now: datetime,
    ) -> list[MemoryHit]:
        """The memories most similar in meaning to a query, best first.

        Restricted to vectors produced by the model now in use: comparing across
        embedding models yields distances that look plausible and mean nothing.
        """
        # Cosine distance (0..2) converted to similarity.
        distance = Memory.embedding.cosine_distance(query_embedding)

        statement = (
            select(Memory, distance.label("distance"))
            .where(
                *usable_memories(user_id, now),
                Memory.embedding.is_not(None),
                Memory.embedding_model == embedding_model,
            )
            .order_by(distance)
            .limit(limit)
        )

        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()

        hits = [
            MemoryHit(
                id=memory.id,
                content=memory.content,
                memory_type=memory.memory_type,
                importance=memory.importance,
                created_at=memory.created_at,
                score=1.0 - float(row_distance),
            )
            for memory, row_distance in rows
        ]

        # Threshold applied after ranking; it's a product decision.
        return [hit for hit in hits if hit.score >= min_similarity]
