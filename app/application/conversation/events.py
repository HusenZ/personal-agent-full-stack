from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.application.conversation.dto import ChatResult
from app.graph.conversation.graph import (
    GENERATE_REPLY,
    GRADE_EVIDENCE,
    RETRIEVE_CONTEXT,
    RETRY_RETRIEVAL,
    UNDERSTAND_QUERY,
    VERIFY_ANSWER,
)


class TurnEventType(StrEnum):
    QUERY_STARTED = "query_started"
    QUERY_UNDERSTOOD = "query_understood"
    RETRIEVAL_COMPLETED = "retrieval_completed"
    EVIDENCE_GRADED = "evidence_graded"
    RETRIEVAL_RETRIED = "retrieval_retried"
    DRAFT_COMPLETED = "draft_completed"
    VERIFICATION_COMPLETED = "verification_completed"


@dataclass(frozen=True)
class TurnProgress:
    """A step of the turn finishing, described by metadata alone.

    Never carries text the model wrote. A draft is only shown once it has passed
    verification, and that happens after the graph has finished; a progress
    event carrying a draft would show the user exactly what verification exists
    to stop.
    """

    type: TurnEventType
    data: dict[str, Any]


@dataclass(frozen=True)
class TurnCompleted:
    """The turn finished and its exchange is stored."""

    result: ChatResult


type TurnEvent = TurnProgress | TurnCompleted


def describe_step(node: str, update: dict[str, Any] | None) -> TurnProgress | None:
    """What a client may learn from one node finishing, or None for nothing.

    Allow-listed field by field rather than forwarding the update: graph state
    holds drafts, memories and rewritten queries, and a new field added to it
    must not reach clients just because a node returned it.
    """
    values = update or {}

    if node == UNDERSTAND_QUERY:
        plan = values.get("plan")
        needs = [need.value for need in plan.needs] if plan is not None else []
        return TurnProgress(TurnEventType.QUERY_UNDERSTOOD, {"needs": needs})

    if node == RETRIEVE_CONTEXT:
        return TurnProgress(
            TurnEventType.RETRIEVAL_COMPLETED,
            {
                "memories": len(values.get("memories") or []),
                "timeline_entries": len(values.get("timeline") or []),
                "has_totals": values.get("totals") is not None,
            },
        )

    if node == GRADE_EVIDENCE:
        grade = values.get("evidence_grade")
        return TurnProgress(
            TurnEventType.EVIDENCE_GRADED,
            {
                "memories_kept": len(values.get("memories") or []),
                # None when grading failed and the memories were kept as found.
                "sufficient": grade.sufficient if grade is not None else None,
            },
        )

    if node == RETRY_RETRIEVAL:
        return TurnProgress(
            TurnEventType.RETRIEVAL_RETRIED,
            {
                "attempt": values.get("retrieval_attempts"),
                "memories": len(values.get("memories") or []),
            },
        )

    if node == GENERATE_REPLY:
        return TurnProgress(
            TurnEventType.DRAFT_COMPLETED,
            {
                "attempt": values.get("generation_attempts"),
                "failed": bool(values.get("draft_failed")),
            },
        )

    if node == VERIFY_ANSWER:
        return TurnProgress(
            TurnEventType.VERIFICATION_COMPLETED,
            {"grounded": values.get("grounded")},
        )

    return None
