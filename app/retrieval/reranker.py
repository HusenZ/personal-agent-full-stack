import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.retrieval.types import MemoryHit


@dataclass(frozen=True)
class RerankWeights:
    """How much each signal counts in the final ordering.

    Relevance is weighted to dominate. Every candidate has already cleared a
    relevance gate in its first-stage retriever, so importance and recency only
    decide between memories that are all genuinely on topic; they must never let
    an important but irrelevant memory outrank a relevant one.
    """

    relevance: float = 0.75
    importance: float = 0.15
    recency: float = 0.10

    # Recency half-life: a three-month-old memory counts half.
    recency_half_life_days: float = 90.0

    def __post_init__(self) -> None:
        weights = (self.relevance, self.importance, self.recency)
        if any(weight < 0 for weight in weights):
            raise ValueError("weights must not be negative")
        if not math.isclose(sum(weights), 1.0, abs_tol=1e-9):
            raise ValueError("weights must sum to 1")
        if self.recency_half_life_days <= 0:
            raise ValueError("recency_half_life_days must be positive")


DEFAULT_RERANK_WEIGHTS = RerankWeights()


@dataclass(frozen=True)
class RerankedHit:
    hit: MemoryHit
    relevance: float
    score: float


class Reranker(Protocol):
    """Reorders an already-relevant candidate set.

    A seam because a learned reranker, a cross-encoder or a model call could each
    replace the feature-based one below; evaluation, not assumption, should
    decide whether the extra latency buys better answers.
    """

    def rerank(
        self, hits: list[MemoryHit], fused: dict[uuid.UUID, float], *, max_fused: float
    ) -> list[RerankedHit]: ...


class FeatureReranker:
    """Orders candidates by relevance, then importance and recency.

    Deterministic and free, which matters on a path that already spends a
    planning call, an embedding call and a completion per turn. It uses exactly
    the signals the first-stage retrievers ignore: how much a memory should shape
    answers, and how current it is.
    """

    def __init__(
        self, weights: RerankWeights = DEFAULT_RERANK_WEIGHTS, now: datetime | None = None
    ):
        self._weights = weights
        self._now = now

    def rerank(
        self, hits: list[MemoryHit], fused: dict[uuid.UUID, float], *, max_fused: float
    ) -> list[RerankedHit]:
        now = self._now or datetime.now(tz=UTC)

        scored = []
        for hit in hits:
            # Normalise against the best possible score, not the batch's best.
            relevance = min(1.0, fused.get(hit.id, 0.0) / max_fused) if max_fused else 0.0
            recency = _recency(hit.created_at, now, self._weights.recency_half_life_days)

            score = (
                self._weights.relevance * relevance
                + self._weights.importance * hit.importance
                + self._weights.recency * recency
            )
            scored.append(RerankedHit(hit=hit, relevance=relevance, score=score))

        # Id as the final key so equal scores order the same way on every run.
        return sorted(scored, key=lambda item: (-item.score, str(item.hit.id)))


def _recency(created_at: datetime, now: datetime, half_life_days: float) -> float:
    age_days = max(0.0, (now - created_at).total_seconds() / 86_400)
    return float(0.5 ** (age_days / half_life_days))
