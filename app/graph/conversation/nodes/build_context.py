from app.domain.conversation.context import (
    ComputedTotals,
    ContextBudget,
    ProfileContext,
    build_conversation_context,
)
from app.domain.temporal.ranges import describe, resolve_timezone
from app.graph.conversation.state import ConversationNode, ConversationTurnState
from app.retrieval.structured import render_summary
from app.retrieval.temporal import render_timeline


def build_build_context_node(budget: ContextBudget) -> ConversationNode:
    async def build_context(state: ConversationTurnState) -> ConversationTurnState:
        """Assemble the bounded view of the conversation the model will see.

        Bounded here rather than at the query, so that what the model was shown
        is decided in one place and can be asserted against in tests.
        """
        zone = resolve_timezone(state.get("timezone"))
        time_range = state.get("time_range")
        period = describe(time_range, zone) if time_range is not None else ""

        summary = state.get("totals")
        totals = (
            ComputedTotals(period=period, rendered=render_summary(summary))
            if summary is not None and not summary.is_empty()
            else None
        )

        grade = state.get("evidence_grade")

        entries = tuple((state.get("timeline") or [])[: budget.max_timeline_entries])

        context = build_conversation_context(
            profile=state.get("profile") or ProfileContext(),
            history=list(state.get("history") or []),
            current_message=state.get("message") or "",
            budget=budget,
            memories=list(state.get("memories") or []),
            timeline=render_timeline(entries),
            timeline_period=period,
            totals=totals,
            # Still insufficient here means the retry didn't help.
            evidence_incomplete=grade is not None and not grade.sufficient,
        )

        return {"context": context}

    return build_context
