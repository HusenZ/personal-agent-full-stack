from dataclasses import dataclass
from enum import StrEnum

from app.domain.analysis.claims import AnalysisClaim, ClaimType
from app.domain.analysis.evidence import EvidenceBundle

# Claim types that must cite evidence.
_REQUIRES_EVIDENCE = frozenset(
    {ClaimType.FACT, ClaimType.OBSERVATION, ClaimType.INFERENCE, ClaimType.RECOMMENDATION}
)

# A fact is a restatement of what the user told us; anything hedged is not a fact.
MIN_CONFIDENCE_FOR_FACT = 0.7


class RejectionReason(StrEnum):
    UNKNOWN_EVIDENCE = "unknown_evidence"
    MISSING_EVIDENCE = "missing_evidence"
    UNDERCONFIDENT_FACT = "underconfident_fact"


@dataclass(frozen=True)
class RejectedClaim:
    claim: AnalysisClaim
    reason: RejectionReason


@dataclass(frozen=True)
class GroundingReport:
    accepted: tuple[AnalysisClaim, ...]
    rejected: tuple[RejectedClaim, ...]

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    def is_usable(self) -> bool:
        """Whether the surviving claims amount to an analysis worth returning."""
        return bool(self.accepted)


def check_claim(claim: AnalysisClaim, evidence: EvidenceBundle) -> RejectionReason | None:
    """Return why a claim is not admissible, or None when it is.

    Grounding is decided here rather than trusted to the model: a model asked to
    cite its evidence will happily cite evidence that does not exist.
    """
    unknown = [item_id for item_id in claim.evidence_ids if item_id not in evidence.ids]
    if unknown:
        return RejectionReason.UNKNOWN_EVIDENCE

    if claim.claim_type in _REQUIRES_EVIDENCE and not claim.evidence_ids:
        return RejectionReason.MISSING_EVIDENCE

    if claim.claim_type is ClaimType.FACT and claim.confidence < MIN_CONFIDENCE_FOR_FACT:
        return RejectionReason.UNDERCONFIDENT_FACT

    return None


def ground_claims(claims: list[AnalysisClaim], evidence: EvidenceBundle) -> GroundingReport:
    """Split generated claims into those the evidence supports and those it does not.

    Unsupported claims are dropped rather than downgraded: silently relabelling
    an invented fact as an inference would keep the invention in the analysis.
    """
    accepted: list[AnalysisClaim] = []
    rejected: list[RejectedClaim] = []

    for claim in claims:
        reason = check_claim(claim, evidence)
        if reason is None:
            accepted.append(claim)
        else:
            rejected.append(RejectedClaim(claim=claim, reason=reason))

    return GroundingReport(accepted=tuple(accepted), rejected=tuple(rejected))


def overall_confidence(claims: tuple[AnalysisClaim, ...]) -> float:
    """Summarise how well supported an analysis is as a whole.

    Recommendations are excluded: they are proposals about what to do next, not
    assertions about the user, so their confidence says nothing about how well
    the system understands them.
    """
    assertions = [
        claim.confidence for claim in claims if claim.claim_type is not ClaimType.RECOMMENDATION
    ]
    if not assertions:
        return 0.0

    return sum(assertions) / len(assertions)
