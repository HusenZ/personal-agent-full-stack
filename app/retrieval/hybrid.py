import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.retrieval.fusion import RRF_K, best_possible_score, reciprocal_rank_fusion
from app.retrieval.keyword import KeywordMemoryRetriever
from app.retrieval.reranker import FeatureReranker, Reranker
from app.retrieval.semantic import SemanticMemoryRetriever
from app.retrieval.types import MemoryHit, RetrievalSource, RetrievedMemory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HybridConfig:
    """How wide each first stage casts, and how much survives to the model.

    The candidate pools are deliberately wider than the final limit: fusion can
    only promote a memory that some retriever found, so a pool as narrow as the
    output would leave nothing for the second stage to decide between. These
    numbers are starting points; PRD 21 requires them to be benchmarked rather
    than assumed, which is evaluation's job.
    """

    semantic_candidates: int = 20
    keyword_candidates: int = 20
    limit: int = 6
    min_similarity: float = 0.25
    rrf_k: int = RRF_K

    @classmethod
    def for_limit(cls, limit: int, min_similarity: float) -> "HybridConfig":
        """The configuration production runs for a given output size.

        Pools wider than the output, so fusion has something to choose between;
        a pool as narrow as the result leaves no second stage. Shared by the API
        and the evaluation, so what is measured is what is served.
        """
        pool = max(20, limit * 3)
        return cls(
            limit=limit,
            semantic_candidates=pool,
            keyword_candidates=pool,
            min_similarity=min_similarity,
        )

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("limit must be at least 1")
        if self.semantic_candidates < self.limit or self.keyword_candidates < self.limit:
            raise ValueError("candidate pools must be at least as large as the limit")


class MemoryRetriever(Protocol):
    """Finds the memories relevant to a message.

    Takes the embedding already computed, or None when it could not be: the
    caller owns the network call so it can overlap it with other work, and a
    retriever that cannot embed should still do what it can without.
    """

    async def retrieve(
        self,
        user_id: uuid.UUID,
        query_text: str,
        query_embedding: list[float] | None,
        *,
        now: datetime | None = None,
    ) -> list[RetrievedMemory]: ...


class HybridMemoryRetriever:
    """Semantic and keyword search, fused by rank and reranked.

    The two stages run one after the other on purpose. Each opens its own short
    session, so they could overlap, but only by holding two pooled connections
    per search to save a few milliseconds.
    """

    def __init__(
        self,
        semantic: SemanticMemoryRetriever,
        keyword: KeywordMemoryRetriever,
        *,
        embedding_model: str,
        config: HybridConfig | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self._semantic = semantic
        self._keyword = keyword
        self._embedding_model = embedding_model
        self._config = config or HybridConfig()
        self._reranker = reranker or FeatureReranker()

    async def retrieve(
        self,
        user_id: uuid.UUID,
        query_text: str,
        query_embedding: list[float] | None,
        *,
        now: datetime | None = None,
    ) -> list[RetrievedMemory]:
        moment = now or datetime.now(tz=UTC)
        started = time.perf_counter()

        keyword_hits = await self._keyword.search(
            user_id, query_text, limit=self._config.keyword_candidates, now=moment
        )
        keyword_ms = (time.perf_counter() - started) * 1000

        semantic_hits: list[MemoryHit] = []
        if query_embedding is not None:
            semantic_hits = await self._semantic.search(
                user_id,
                query_embedding,
                embedding_model=self._embedding_model,
                limit=self._config.semantic_candidates,
                min_similarity=self._config.min_similarity,
                now=moment,
            )
        semantic_ms = (time.perf_counter() - started) * 1000 - keyword_ms

        rankings = {
            RetrievalSource.SEMANTIC: [hit.id for hit in semantic_hits],
            RetrievalSource.KEYWORD: [hit.id for hit in keyword_hits],
        }
        fused = reciprocal_rank_fusion(rankings, k=self._config.rrf_k)

        # Normalise against both sources so single-source matches don't max out.
        max_fused = best_possible_score(len(rankings), k=self._config.rrf_k)

        by_id: dict[uuid.UUID, MemoryHit] = {}
        for hit in [*semantic_hits, *keyword_hits]:
            by_id.setdefault(hit.id, hit)

        reranked = self._reranker.rerank(list(by_id.values()), fused, max_fused=max_fused)[
            : self._config.limit
        ]

        similarity = {hit.id: hit.score for hit in semantic_hits}
        found_by = {
            item_id: tuple(source for source, ranking in rankings.items() if item_id in ranking)
            for item_id in by_id
        }

        results = [
            RetrievedMemory(
                id=item.hit.id,
                content=item.hit.content,
                memory_type=item.hit.memory_type,
                importance=item.hit.importance,
                relevance=item.score,
                sources=found_by[item.hit.id],
                similarity=similarity.get(item.hit.id),
            )
            for item in reranked
        ]

        logger.info(
            "hybrid_memory_retrieved",
            extra={
                "user_id": str(user_id),
                "semantic_candidates": len(semantic_hits),
                "keyword_candidates": len(keyword_hits),
                "found_by_both": sum(1 for sources in found_by.values() if len(sources) == 2),
                "returned": len(results),
                "semantic_skipped": query_embedding is None,
                "keyword_ms": round(keyword_ms, 2),
                "semantic_ms": round(semantic_ms, 2),
            },
        )

        return results
