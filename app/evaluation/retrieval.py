"""Retrieval quality per strategy, measured on the labelled dataset.

The four strategies the guidelines require to be compared, each built exactly as
production builds it apart from the one thing being varied, so a difference in
the numbers is attributable to that one thing.
"""

import time
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.evaluation.dataset import CaseCategory, Dataset
from app.evaluation.metrics import mean, ndcg_at_k, percentile, precision_at_k, recall_at_k
from app.evaluation.metrics import reciprocal_rank as rr
from app.evaluation.seeding import SeededPersona, remove_evaluation_users, seed_persona
from app.infrastructure.llm.embeddings import EmbeddingProvider
from app.retrieval.hybrid import HybridConfig, HybridMemoryRetriever
from app.retrieval.keyword import KeywordMemoryRetriever
from app.retrieval.reranker import FeatureReranker, RerankWeights
from app.retrieval.semantic import SemanticMemoryRetriever


class Strategy(StrEnum):
    VECTOR = "vector"
    KEYWORD = "keyword"
    # Rank fusion alone: the reranker reduced to relevance only.
    HYBRID = "hybrid"
    # What production runs.
    HYBRID_RERANKED = "hybrid_reranked"


_RELEVANCE_ONLY = RerankWeights(relevance=1.0, importance=0.0, recency=0.0)


async def _rank(
    strategy: Strategy,
    sessions: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    question: str,
    embedding: list[float] | None,
    *,
    config: HybridConfig,
    embedding_model: str,
    now: datetime,
) -> list[uuid.UUID]:
    if strategy is Strategy.VECTOR:
        if embedding is None:
            return []
        hits = await SemanticMemoryRetriever(sessions).search(
            user_id,
            embedding,
            embedding_model=embedding_model,
            limit=config.limit,
            min_similarity=config.min_similarity,
            now=now,
        )
        return [hit.id for hit in hits]

    if strategy is Strategy.KEYWORD:
        hits = await KeywordMemoryRetriever(sessions).search(
            user_id, question, limit=config.limit, now=now
        )
        return [hit.id for hit in hits]

    reranker = (
        FeatureReranker(_RELEVANCE_ONLY, now=now)
        if strategy is Strategy.HYBRID
        else FeatureReranker(now=now)
    )
    retriever = HybridMemoryRetriever(
        SemanticMemoryRetriever(sessions),
        KeywordMemoryRetriever(sessions),
        embedding_model=embedding_model,
        config=config,
        reranker=reranker,
    )
    results = await retriever.retrieve(user_id, question, embedding, now=now)
    return [result.id for result in results]


async def evaluate_retrieval(
    app: FastAPI,
    dataset: Dataset,
    *,
    embeddings: EmbeddingProvider,
    embedding_model: str,
    config: HybridConfig,
    run_tag: str | None = None,
) -> dict[str, Any]:
    """Seed the dataset's personas, rank every case with every strategy, report."""
    tag = run_tag or uuid.uuid4().hex[:8]
    now = datetime.now(tz=UTC)
    k = config.limit

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://evaluation"
    ) as client:
        seeded: dict[str, SeededPersona] = {}
        for persona in dataset.personas:
            seeded[persona.key] = await seed_persona(
                app,
                client,
                persona,
                embeddings=embeddings,
                embedding_model=embedding_model,
                run_tag=tag,
                now=now,
            )

    rows: list[dict[str, Any]] = []
    embed_ms: list[float] = []
    try:
        sessions = app.state.database_session_factory
        for case in dataset.cases:
            if not case.relevant and case.category is not CaseCategory.UNANSWERABLE:
                # Exact-figure questions are measured by the answer evaluation instead.
                continue
            owner = seeded[case.persona]

            started = time.perf_counter()
            embedding = await embeddings.embed_query(case.question)
            embed_ms.append((time.perf_counter() - started) * 1000)

            for strategy in Strategy:
                started = time.perf_counter()
                ranked_ids = await _rank(
                    strategy,
                    sessions,
                    owner.user_id,
                    case.question,
                    embedding,
                    config=config,
                    embedding_model=embedding_model,
                    now=now,
                )
                latency = (time.perf_counter() - started) * 1000
                ranked = [owner.key_of(item) or str(item) for item in ranked_ids]
                rows.append(
                    _score(case.id, case.category, strategy, ranked, case.relevant, k)
                    | {"latency_ms": round(latency, 2)}
                )
    finally:
        await remove_evaluation_users(app)

    return {
        "kind": "retrieval",
        "dataset": dataset.name,
        "dataset_version": dataset.version,
        "dataset_reviewed": dataset.reviewed,
        "created_at": now.isoformat(),
        "config": {
            "k": k,
            "embedding_model": embedding_model,
            "min_similarity": config.min_similarity,
            "rrf_k": config.rrf_k,
            "semantic_candidates": config.semantic_candidates,
            "keyword_candidates": config.keyword_candidates,
        },
        "query_embedding_ms": _latency(embed_ms),
        "summary": {
            strategy.value: _summarise([r for r in rows if r["strategy"] == strategy])
            for strategy in Strategy
        },
        "by_category": {
            strategy.value: {
                category.value: _summarise(
                    [r for r in rows if r["strategy"] == strategy and r["category"] == category]
                )
                for category in CaseCategory
                if any(r["category"] == category for r in rows)
            }
            for strategy in Strategy
        },
        "cases": rows,
    }


def _score(
    case_id: str,
    category: CaseCategory,
    strategy: Strategy,
    ranked: list[str],
    relevance: dict[str, int],
    k: int,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "case": case_id,
        "category": category.value,
        "strategy": strategy.value,
        "ranked": ranked,
        "returned": len(ranked),
    }
    if relevance:
        row |= {
            "recall": recall_at_k(ranked, relevance, k),
            "precision": precision_at_k(ranked, relevance, k),
            "reciprocal_rank": rr(ranked, relevance),
            "ndcg": ndcg_at_k(ranked, relevance, k),
        }
    return row


def _summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if "recall" in row]
    unanswerable = [row for row in rows if "recall" not in row]
    summary: dict[str, Any] = {"cases": len(scored)}
    if scored:
        summary |= {
            "recall_at_k": round(mean([row["recall"] for row in scored]), 4),
            "precision_at_k": round(mean([row["precision"] for row in scored]), 4),
            "mrr": round(mean([row["reciprocal_rank"] for row in scored]), 4),
            "ndcg_at_k": round(mean([row["ndcg"] for row in scored]), 4),
        }
    if unanswerable:
        # Nothing is relevant here, so anything returned is noise. Lower is better.
        summary["noise_on_unanswerable"] = round(
            mean([float(row["returned"]) for row in unanswerable]), 4
        )
    if rows and "latency_ms" in rows[0]:
        summary |= {
            f"latency_{key}": value
            for key, value in _latency([row["latency_ms"] for row in rows]).items()
        }
    return summary


def _latency(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {"p50_ms": round(percentile(values, 50), 2), "p95_ms": round(percentile(values, 95), 2)}
