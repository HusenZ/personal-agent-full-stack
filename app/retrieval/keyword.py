import re
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.database.models import Memory
from app.retrieval.filters import usable_memories
from app.retrieval.types import MemoryHit

# Must match the search_vector config or stemming won't line up.
TEXT_SEARCH_CONFIG = "english"

# Cap query terms so long messages don't match everything.
MAX_QUERY_TERMS = 32

_TERM = re.compile(r"[a-z0-9]+")


def build_query_terms(query_text: str) -> str:
    """Turn free text into an OR-query of its words.

    OR rather than the AND that websearch_to_tsquery produces. A conversational
    question carries many words a memory will not, and requiring all of them
    would make keyword search find almost nothing; ranking still favours the
    memories that match more of them.

    Only runs of letters and digits survive, so the result is valid tsquery
    syntax whatever the user typed: there is no stray colon, quote or operator
    left to raise a syntax error. Stopwords are left for Postgres to drop, since
    its list is the one the stored vectors were built with.
    """
    terms: list[str] = []
    for term in _TERM.findall(query_text.lower()):
        if term not in terms:
            terms.append(term)
        if len(terms) == MAX_QUERY_TERMS:
            break

    return " | ".join(terms)


class KeywordMemoryRetriever:
    """Finds a user's memories by the words in them.

    Complements semantic search rather than duplicating it. Embeddings blur
    names, identifiers and rare terms into their neighbourhood, which is exactly
    where an exact word match is most useful: a question about "Rex" should find
    the memory about Rex, not the ones about dogs in general. It also needs no
    network call, so it keeps working when the embedding provider does not.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        # Own short session per search, so no connection is held during model calls.
        self._sessions = sessions

    async def search(
        self, user_id: uuid.UUID, query_text: str, *, limit: int, now: datetime
    ) -> list[MemoryHit]:
        """Memories containing the query's words, best first."""
        terms = build_query_terms(query_text)
        if not terms:
            return []

        query = func.to_tsquery(TEXT_SEARCH_CONFIG, terms)
        rank = func.ts_rank_cd(Memory.search_vector, query)

        statement = (
            select(Memory, rank.label("rank"))
            .where(*usable_memories(user_id, now), Memory.search_vector.op("@@")(query))
            .order_by(rank.desc(), Memory.id)
            .limit(limit)
        )

        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()

        return [
            MemoryHit(
                id=memory.id,
                content=memory.content,
                memory_type=memory.memory_type,
                importance=memory.importance,
                created_at=memory.created_at,
                score=float(row_rank),
            )
            for memory, row_rank in rows
        ]
