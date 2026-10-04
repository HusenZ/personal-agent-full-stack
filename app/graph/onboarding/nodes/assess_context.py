from app.domain.onboarding.assessment import assess_coverage, decide_completeness
from app.domain.onboarding.context import CollectedContext
from app.domain.onboarding.policy import OnboardingPolicy
from app.graph.onboarding.state import OnboardingNode, OnboardingTurnState


def build_assess_context_node(policy: OnboardingPolicy) -> OnboardingNode:
    async def assess_context(state: OnboardingTurnState) -> OnboardingTurnState:
        """Score what is known and decide whether to keep interviewing.

        Entirely deterministic: the decision to stop asking must be reproducible
        and cheap to audit, not a model's opinion.
        """
        coverage = assess_coverage(state.get("collected") or CollectedContext())
        decision = decide_completeness(
            coverage, questions_asked=state.get("questions_asked") or 0, policy=policy
        )

        return {
            "coverage": {
                dimension.value: score for dimension, score in coverage.per_dimension.items()
            },
            "overall_confidence": coverage.overall_confidence,
            "is_complete": decision.is_complete,
            "completion_reason": decision.reason,
        }

    return assess_context
