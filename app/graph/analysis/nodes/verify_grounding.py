import logging
from collections import Counter

from app.domain.analysis.evidence import EvidenceBundle
from app.domain.analysis.grounding import ground_claims, overall_confidence
from app.domain.analysis.policy import AnalysisPolicy
from app.graph.analysis.state import AnalysisNode, AnalysisState
from app.infrastructure.llm.prompts.analysis import describe_rejections

logger = logging.getLogger(__name__)


def build_verify_grounding_node(policy: AnalysisPolicy) -> AnalysisNode:
    async def verify_grounding(state: AnalysisState) -> AnalysisState:
        """Keep only claims the evidence supports, and judge whether to retry.

        The better of the attempts so far is retained, so a weaker retry cannot
        lose a usable analysis that the first attempt already produced.
        """
        evidence = state.get("evidence") or EvidenceBundle(items=())
        report = ground_claims(state.get("claims") or [], evidence)

        previous = state.get("claims_accepted_best") or ()
        best = report.accepted if len(report.accepted) >= len(previous) else previous

        attempt = state.get("attempt") or 0
        attempts_remain = attempt < policy.max_generation_attempts
        meets_bar = len(best) >= policy.min_claims

        if report.rejected:
            reasons = Counter(rejected.reason.value for rejected in report.rejected)
            logger.warning(
                "analysis_claims_rejected",
                extra={
                    "attempt": attempt,
                    "rejected_count": report.rejected_count,
                    "accepted_count": len(report.accepted),
                    "reasons": dict(reasons),
                },
            )
            feedback = describe_rejections(dict(reasons))
        else:
            feedback = None

        return {
            "claims_accepted_best": best,
            "claims": list(best),
            "rejected": list(report.rejected),
            "rejection_feedback": feedback,
            "overall_confidence": overall_confidence(best),
            "degraded": not meets_bar and not attempts_remain,
        }

    return verify_grounding
