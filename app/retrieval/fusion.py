import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence

from app.retrieval.types import RetrievalSource

# Standard RRF constant from the original paper.
RRF_K = 60


def reciprocal_rank_fusion(
    rankings: Mapping[RetrievalSource, Sequence[uuid.UUID]], k: int = RRF_K
) -> dict[uuid.UUID, float]:
    """Combine several rankings into one score per item.

    Positions rather than scores, deliberately: a cosine similarity and a
    full-text rank are on unrelated scales, and any attempt to add them would
    need calibration that has not been done. A memory both retrievers rank
    highly accumulates from both, which is the property that matters.
    """
    if k < 1:
        raise ValueError("k must be at least 1")

    fused: dict[uuid.UUID, float] = defaultdict(float)

    for ranking in rankings.values():
        seen: set[uuid.UUID] = set()
        for position, item in enumerate(ranking, start=1):
            # A retriever that returned the same item twice should not vote twice.
            if item in seen:
                continue
            seen.add(item)
            fused[item] += 1.0 / (k + position)

    return dict(fused)


def best_possible_score(source_count: int, k: int = RRF_K) -> float:
    """The fused score of an item ranked first by every retriever."""
    return source_count / (k + 1)
