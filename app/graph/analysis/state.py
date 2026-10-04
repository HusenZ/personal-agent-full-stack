from typing import Protocol, TypedDict

from app.domain.analysis.claims import AnalysisClaim
from app.domain.analysis.evidence import EvidenceBundle, RawEvidence
from app.domain.analysis.grounding import RejectedClaim


class AnalysisState(TypedDict, total=False):
    """State for one analysis generation, including its retry loop."""

    user_id: str

    raw_evidence: RawEvidence
    evidence: EvidenceBundle

    attempt: int
    rejection_feedback: str | None

    claims: list[AnalysisClaim]

    # Best attempt so far, so a worse retry can't replace it.
    claims_accepted_best: tuple[AnalysisClaim, ...]
    rejected: list[RejectedClaim]
    overall_confidence: float

    # True when returned below the bar because the budget ran out.
    degraded: bool
    errors: list[str]


class AnalysisNode(Protocol):
    """What every node in the analysis graph looks like.

    Declared as a Protocol with a named ``state`` parameter rather than a bare
    Callable: LangGraph matches node callables structurally and rejects a
    positional-only signature.
    """

    async def __call__(self, state: AnalysisState) -> AnalysisState: ...
