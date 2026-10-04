import logging
import time

from app.domain.retrieval.grading import EvidenceGrade, EvidencePolicy, apply_grade
from app.graph.conversation.state import ConversationNode, ConversationTurnState
from app.infrastructure.llm.exceptions import LLMError
from app.infrastructure.llm.prompts.retrieval import build_grade_prompt
from app.infrastructure.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


def build_grade_evidence_node(provider: LLMProvider, policy: EvidencePolicy) -> ConversationNode:
    async def grade_evidence(state: ConversationTurnState) -> ConversationTurnState:
        """Judge whether the recalled memories can answer the question.

        A failure here fails open: the memories go forward as retrieved and no
        retry is attempted. That is exactly how answers worked before grading,
        so a grading outage costs precision rather than the answer.
        """
        candidates = (state.get("memories") or [])[: policy.max_graded_memories]
        searches = state.get("searches") or []

        started = time.perf_counter()
        try:
            grade = await provider.generate_structured(
                build_grade_prompt(
                    state.get("message") or "",
                    candidates,
                    searches,
                    has_totals=_has_totals(state),
                    has_timeline=bool(state.get("timeline")),
                ),
                EvidenceGrade,
            )
        except LLMError as error:
            logger.warning("evidence_grading_failed", extra={"error_type": type(error).__name__})
            return {
                "memories": candidates,
                "evidence_grade": None,
                "errors": [*(state.get("errors") or []), "evidence_grading_failed"],
            }

        kept = apply_grade(candidates, grade)

        logger.info(
            "evidence_graded",
            extra={
                "user_id": state.get("user_id"),
                "conversation_id": state.get("conversation_id"),
                "attempt": state.get("retrieval_attempts") or 0,
                "graded": len(candidates),
                "kept": len(kept),
                "sufficient": grade.sufficient,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )

        return {"memories": kept, "evidence_grade": grade}

    return grade_evidence


def _has_totals(state: ConversationTurnState) -> bool:
    summary = state.get("totals")
    return summary is not None and not summary.is_empty()
