from typing import Protocol, TypedDict

from app.domain.conversation.context import (
    ContextMessage,
    ConversationContext,
    ProfileContext,
    RecalledMemory,
)
from app.domain.conversation.verification import UnsupportedStatement
from app.domain.retrieval.grading import EvidenceGrade
from app.domain.retrieval.plan import QueryPlan
from app.domain.temporal.ranges import TimeRange
from app.retrieval.structured import ActivitySummary
from app.retrieval.temporal import TimelineEntry


class ConversationTurnState(TypedDict, total=False):
    """State for answering one message.

    The retrieval phases add memories, events and evidence alongside the profile;
    the shape is a skeleton that grows rather than one that gets replaced.
    """

    user_id: str
    conversation_id: str

    profile: ProfileContext
    history: list[ContextMessage]
    message: str

    # The user's time zone decides what "last week" means.
    timezone: str

    plan: QueryPlan
    time_range: TimeRange

    memories: list[RecalledMemory]
    timeline: list[TimelineEntry]
    totals: ActivitySummary | None

    # Searches tried this turn, so the grader doesn't propose them again.
    searches: list[str]

    # Loop counter; advanced on every retry so the bound always holds.
    retrieval_attempts: int

    # Latest grade; None means memories were used as retrieved.
    evidence_grade: EvidenceGrade | None

    context: ConversationContext
    reply: str

    # Loop counter, advanced by every generation.
    generation_attempts: int

    # True when the latest generation produced no text.
    draft_failed: bool

    # Confirmed findings the next draft must avoid.
    findings: list[UnsupportedStatement]

    # Best draft so far and its finding count.
    best_reply: str
    best_findings: int

    # None when not verified (off, failed, or fallback reply).
    grounded: bool | None

    # True when the reply is the fallback text.
    degraded: bool
    errors: list[str]


class ConversationNode(Protocol):
    """What every node in the conversation graph looks like.

    Declared as a Protocol with a named ``state`` parameter rather than a bare
    Callable: LangGraph matches node callables structurally and rejects a
    positional-only signature.
    """

    async def __call__(self, state: ConversationTurnState) -> ConversationTurnState: ...
