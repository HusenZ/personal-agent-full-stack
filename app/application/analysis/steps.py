"""What an analysis trace may keep about each step of the analysis graph."""

from collections import Counter
from typing import Any

from app.domain.analysis.claims import AnalysisClaim
from app.domain.analysis.evidence import EvidenceBundle
from app.domain.analysis.grounding import RejectedClaim
from app.graph.analysis.graph import BUILD_EVIDENCE, GENERATE_ANALYSIS, VERIFY_GROUNDING


def describe_analysis_step(node: str, update: dict[str, Any] | None) -> dict[str, Any] | None:
    """Counts and outcomes for one node, or None for nothing.

    Allow-listed field by field, as for conversation turns. Claims and evidence
    are what the model wrote and what the user said, so only their number is
    stored; claim types and rejection reasons are fixed vocabularies defined in
    the domain, so they are.
    """
    values = update or {}

    if node == BUILD_EVIDENCE:
        evidence: EvidenceBundle | None = values.get("evidence")
        return {"evidence_items": len(evidence.items) if evidence is not None else 0}

    if node == GENERATE_ANALYSIS:
        claims: list[AnalysisClaim] = values.get("claims") or []
        return {
            "attempt": values.get("attempt"),
            "claims": len(claims),
            # The node returns errors only when its model call failed.
            "failed": "errors" in values,
        }

    if node == VERIFY_GROUNDING:
        kept: tuple[AnalysisClaim, ...] = values.get("claims_accepted_best") or ()
        rejected: list[RejectedClaim] = values.get("rejected") or []
        reasons = Counter(item.reason.value for item in rejected)
        types = Counter(claim.claim_type.value for claim in kept)
        return {
            "claims_kept": len(kept),
            "claim_types": dict(sorted(types.items())),
            "rejected": dict(sorted(reasons.items())),
            "overall_confidence": round(float(values.get("overall_confidence") or 0.0), 4),
            "degraded": bool(values.get("degraded")),
        }

    return None
