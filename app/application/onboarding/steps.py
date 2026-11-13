"""What an onboarding trace may keep about each step of the interview graph."""

from typing import Any

from app.graph.onboarding.graph import (
    ASSESS_CONTEXT,
    BUILD_PROFILE,
    EXTRACT_INFORMATION,
    GENERATE_QUESTION,
)
from app.graph.onboarding.state import AskedQuestion, ProfileDraft


def describe_onboarding_step(node: str, update: dict[str, Any] | None) -> dict[str, Any] | None:
    """Counts, scores and outcomes for one node, or None for nothing.

    Allow-listed field by field, as for conversation turns. Interview state
    holds the user's answer, the questions asked and everything extracted from
    them; none of that is stored. Dimensions and completion reasons are fixed
    vocabularies defined in the domain, so they are.
    """
    values = update or {}

    if node == EXTRACT_INFORMATION:
        return {
            "answer_usable": bool(values.get("answer_was_usable")),
            # The node returns errors only when its model call failed.
            "extraction_failed": "errors" in values,
        }

    if node == ASSESS_CONTEXT:
        coverage: dict[str, float] = values.get("coverage") or {}
        return {
            "overall_confidence": round(float(values.get("overall_confidence") or 0.0), 4),
            "coverage": {dimension: round(score, 4) for dimension, score in coverage.items()},
            "is_complete": bool(values.get("is_complete")),
            "completion_reason": values.get("completion_reason"),
        }

    if node == GENERATE_QUESTION:
        question: AskedQuestion | None = values.get("next_question")
        return {
            "dimension": question.dimension.value if question is not None else None,
            "questions_asked": values.get("questions_asked"),
        }

    if node == BUILD_PROFILE:
        draft: ProfileDraft | None = values.get("profile_draft")
        return {
            "goals": len(draft.goals) if draft is not None else 0,
            "constraints": len(draft.constraints) if draft is not None else 0,
            "preferences": len(draft.preferences) if draft is not None else 0,
        }

    return None
