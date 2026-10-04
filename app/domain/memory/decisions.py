from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.memory.pairing import CandidatePairing, ExistingMemory
from app.domain.memory.policy import DURABILITY_BY_TYPE, MemoryPolicy
from app.domain.memory.types import MemoryCandidate


class MemoryVerdict(StrEnum):
    DUPLICATE = "duplicate"
    CONTRADICTS = "contradicts"
    UNRELATED = "unrelated"


class PairJudgment(BaseModel):
    memory_id: str = Field(description="The id of the existing memory being judged.")
    verdict: MemoryVerdict


class MemoryJudgment(BaseModel):
    """The structured-output schema for comparing one candidate to existing memories."""

    judgments: list[PairJudgment] = Field(default_factory=list, max_length=20)


class DecisionKind(StrEnum):
    CREATE = "create"
    REINFORCE = "reinforce"
    SUPERSEDE = "supersede"


@dataclass(frozen=True)
class MemoryDecision:
    kind: DecisionKind
    candidate: MemoryCandidate

    # Reuse the comparison vector instead of embedding the text twice.
    embedding: tuple[float, ...] | None = None

    # REINFORCE: the memory strengthened. SUPERSEDE: the memories replaced.
    reinforces: ExistingMemory | None = None
    supersedes: tuple[ExistingMemory, ...] = ()


def admit(candidate: MemoryCandidate, policy: MemoryPolicy) -> bool:
    """Whether a candidate clears the bar for being remembered at all.

    Both thresholds matter and for different reasons: a low-importance memory
    would dilute every later retrieval, and a low-confidence one is a guess about
    the user, which is worse than having no memory of them.
    """
    return (
        candidate.importance >= policy.min_importance
        and candidate.confidence >= policy.min_confidence
    )


def normalise_durability(candidate: MemoryCandidate) -> MemoryCandidate:
    """Override durability where the type already settles it.

    A stated goal is long term whatever the exchange sounded like; leaving that
    to the model means a goal occasionally expires in a day.
    """
    fixed = DURABILITY_BY_TYPE.get(candidate.memory_type)
    if fixed is None or candidate.durability is fixed:
        return candidate

    return candidate.model_copy(update={"durability": fixed})


def decide(
    pairing: CandidatePairing,
    judgment: MemoryJudgment | None = None,
    embedding: tuple[float, ...] | None = None,
) -> MemoryDecision:
    """Turn a pairing and the model's verdicts into one persistence decision.

    Judgements naming a memory that was not offered for comparison are ignored
    rather than trusted: a model asked to judge a list will occasionally judge
    something that is not on it.
    """
    candidate = normalise_durability(pairing.candidate)

    if pairing.duplicate_of is not None:
        return MemoryDecision(
            kind=DecisionKind.REINFORCE,
            candidate=candidate,
            reinforces=pairing.duplicate_of,
            embedding=embedding,
        )

    offered = {memory.id: memory for memory in pairing.to_judge}
    verdicts = [
        (offered[item.memory_id], item.verdict)
        for item in (judgment.judgments if judgment else [])
        if item.memory_id in offered
    ]

    contradicted = tuple(
        memory for memory, verdict in verdicts if verdict is MemoryVerdict.CONTRADICTS
    )
    if contradicted:
        return MemoryDecision(
            kind=DecisionKind.SUPERSEDE,
            candidate=candidate,
            supersedes=contradicted,
            embedding=embedding,
        )

    for memory, verdict in verdicts:
        if verdict is MemoryVerdict.DUPLICATE:
            return MemoryDecision(
                kind=DecisionKind.REINFORCE,
                candidate=candidate,
                reinforces=memory,
                embedding=embedding,
            )

    return MemoryDecision(kind=DecisionKind.CREATE, candidate=candidate, embedding=embedding)


def reinforced_scores(existing: ExistingMemory, candidate: MemoryCandidate) -> tuple[float, float]:
    """Scores for a memory the user has now expressed twice.

    Taking the maximum rather than averaging: hearing something a second time is
    evidence for it, and should never weaken what was already believed.
    """
    return (
        max(existing.importance, candidate.importance),
        max(existing.confidence, candidate.confidence),
    )
