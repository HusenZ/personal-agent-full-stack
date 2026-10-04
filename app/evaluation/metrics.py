"""Ranking and latency metrics, as pure functions.

``ranked`` is what a retriever returned, best first. ``relevance`` maps each
relevant item to its graded relevance; anything absent is irrelevant.
"""

import math
from collections.abc import Mapping, Sequence


def recall_at_k(ranked: Sequence[str], relevance: Mapping[str, int], k: int) -> float:
    """Share of the relevant items found in the top k."""
    if not relevance:
        raise ValueError("recall is undefined when nothing is relevant")
    found = sum(1 for item in ranked[:k] if item in relevance)
    return found / len(relevance)


def precision_at_k(ranked: Sequence[str], relevance: Mapping[str, int], k: int) -> float:
    """Share of the top k that is relevant.

    Divided by k, not by how many were returned: a retriever that returns one
    relevant item where three were wanted has not been perfectly precise about
    the question it was asked.
    """
    return sum(1 for item in ranked[:k] if item in relevance) / k


def reciprocal_rank(ranked: Sequence[str], relevance: Mapping[str, int]) -> float:
    """1 / the position of the first relevant item, or 0 if there is none."""
    for position, item in enumerate(ranked, start=1):
        if item in relevance:
            return 1 / position
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevance: Mapping[str, int], k: int) -> float:
    """Discounted gain of the top k, against the best ordering possible.

    Graded, so placing the memory that answers the question above one that
    merely helps scores higher than the reverse.
    """
    if not relevance:
        raise ValueError("nDCG is undefined when nothing is relevant")

    def gain(grades: Sequence[int]) -> float:
        return sum(
            ((1 << grade) - 1) / math.log2(position + 1) for position, grade in enumerate(grades, 1)
        )

    actual = gain([relevance.get(item, 0) for item in ranked[:k]])
    ideal = gain(sorted(relevance.values(), reverse=True)[:k])
    return actual / ideal


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile; simple, and exact at the values observed."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("no values")
    return sum(values) / len(values)
