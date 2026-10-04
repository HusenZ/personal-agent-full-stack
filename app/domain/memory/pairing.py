from dataclasses import dataclass

from app.domain.memory.policy import MemoryPolicy
from app.domain.memory.similarity import (
    DUPLICATE_THRESHOLD,
    RELATED_THRESHOLD,
    cosine_similarity,
    similarity,
)
from app.domain.memory.types import MemoryCandidate, MemoryType

# Types where a new belief often replaces an old one, even with no shared words.
_SUPERSEDING_TYPES = frozenset(
    {MemoryType.GOAL, MemoryType.PREFERENCE, MemoryType.CONSTRAINT, MemoryType.PLAN}
)


@dataclass(frozen=True)
class ExistingMemory:
    """The parts of a stored memory that comparison needs.

    A plain value rather than the persistence model, so selection stays testable
    without a database.
    """

    id: str
    content: str
    memory_type: MemoryType
    importance: float
    confidence: float

    # None if never embedded or embedding failed; falls back to token overlap.
    embedding: tuple[float, ...] | None = None


def compare(
    candidate_content: str,
    candidate_embedding: tuple[float, ...] | None,
    existing: ExistingMemory,
) -> float:
    """How alike a candidate and an existing memory are.

    Vector similarity when both sides have an embedding, token overlap
    otherwise. The fallback matters: an embedding call that failed should make
    deduplication worse, not absent.
    """
    if candidate_embedding is not None and existing.embedding is not None:
        return cosine_similarity(candidate_embedding, existing.embedding)

    return similarity(candidate_content, existing.content)


@dataclass(frozen=True)
class CandidatePairing:
    candidate: MemoryCandidate
    duplicate_of: ExistingMemory | None = None
    to_judge: tuple[ExistingMemory, ...] = ()


def pair_candidate(
    candidate: MemoryCandidate,
    existing: list[ExistingMemory],
    policy: MemoryPolicy,
    candidate_embedding: tuple[float, ...] | None = None,
) -> CandidatePairing:
    """Decide what a new memory needs to be compared against.

    Two selections, for two different failure modes. Lexical overlap catches
    restatements of the same sentence. Same-type recency catches disagreements
    phrased in entirely different words, which overlap cannot see and which are
    exactly the case superseding exists for.

    Anything above the duplicate threshold is settled here and never reaches a
    model: identical text does not need judgement.
    """
    scored = sorted(
        ((compare(candidate.content, candidate_embedding, item), item) for item in existing),
        key=lambda pair: (-pair[0], pair[1].id),
    )[: policy.max_comparisons]

    for score, item in scored:
        if score >= DUPLICATE_THRESHOLD and item.memory_type is candidate.memory_type:
            return CandidatePairing(candidate=candidate, duplicate_of=item)

    selected: list[ExistingMemory] = []
    seen: set[str] = set()

    for score, item in scored:
        if score >= RELATED_THRESHOLD and item.id not in seen:
            selected.append(item)
            seen.add(item.id)

    if candidate.memory_type in _SUPERSEDING_TYPES:
        same_type = [
            item
            for _, item in scored
            if item.memory_type is candidate.memory_type and item.id not in seen
        ]
        for item in same_type:
            selected.append(item)
            seen.add(item.id)

    return CandidatePairing(
        candidate=candidate, to_judge=tuple(selected[: policy.max_judged_pairs])
    )
