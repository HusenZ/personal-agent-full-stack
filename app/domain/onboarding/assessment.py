from dataclasses import dataclass
from math import prod

from app.domain.onboarding.context import CollectedContext
from app.domain.onboarding.dimensions import (
    DIMENSION_WEIGHTS,
    REQUIRED_EVIDENCE,
    ContextDimension,
)
from app.domain.onboarding.policy import OnboardingPolicy


@dataclass(frozen=True)
class CoverageReport:
    per_dimension: dict[ContextDimension, float]
    overall_confidence: float


@dataclass(frozen=True)
class CompletenessDecision:
    is_complete: bool
    reason: str
    next_dimension: ContextDimension | None


def score_dimension(confidences: list[float], required_evidence: int) -> float:
    """Combine a dimension's evidence into a single coverage score.

    Two effects are deliberately separated. Strength combines the best pieces of
    evidence with a noisy-OR, so several uncertain statements that agree can add
    up to confidence, and no single statement ever reaches certainty. Sufficiency
    then scales that down when fewer pieces were collected than the dimension
    asks for, so one confident sentence about a goal does not look like a fully
    explored goal.
    """
    if not confidences or required_evidence < 1:
        return 0.0

    strongest = sorted(confidences, reverse=True)[:required_evidence]
    strength = 1.0 - prod(1.0 - confidence for confidence in strongest)
    sufficiency = min(len(confidences), required_evidence) / required_evidence

    return strength * sufficiency


def assess_coverage(context: CollectedContext) -> CoverageReport:
    per_dimension = {
        dimension: score_dimension(context.confidences_for(dimension), REQUIRED_EVIDENCE[dimension])
        for dimension in ContextDimension
    }

    overall = sum(
        score * DIMENSION_WEIGHTS[dimension] for dimension, score in per_dimension.items()
    )

    return CoverageReport(per_dimension=per_dimension, overall_confidence=overall)


def select_next_dimension(coverage: CoverageReport) -> ContextDimension:
    """Pick what to ask about next: the largest weighted gap.

    Weighting the gap rather than taking the lowest raw score stops the agent
    from spending its remaining questions perfecting a dimension that barely
    affects readiness.
    """
    return max(
        ContextDimension,
        key=lambda dimension: (
            DIMENSION_WEIGHTS[dimension] * (1.0 - coverage.per_dimension[dimension]),
            # Enum order breaks ties so the choice is reproducible across runs.
            -list(ContextDimension).index(dimension),
        ),
    )


def decide_completeness(
    coverage: CoverageReport, questions_asked: int, policy: OnboardingPolicy
) -> CompletenessDecision:
    """Decide whether to keep interviewing.

    The question ceiling is checked first and unconditionally: it is the bound
    that guarantees this loop terminates even if the user never says anything
    the extractor can use.
    """
    if questions_asked >= policy.max_questions:
        return CompletenessDecision(
            is_complete=True, reason="question_limit_reached", next_dimension=None
        )

    if questions_asked < policy.min_questions:
        return CompletenessDecision(
            is_complete=False,
            reason="minimum_questions_not_reached",
            next_dimension=select_next_dimension(coverage),
        )

    if coverage.overall_confidence >= policy.confidence_threshold:
        return CompletenessDecision(
            is_complete=True, reason="confidence_threshold_met", next_dimension=None
        )

    return CompletenessDecision(
        is_complete=False,
        reason="confidence_below_threshold",
        next_dimension=select_next_dimension(coverage),
    )
