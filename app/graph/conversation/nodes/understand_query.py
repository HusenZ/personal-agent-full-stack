import logging

from app.domain.retrieval.plan import QueryPlan
from app.graph.conversation.state import ConversationNode, ConversationTurnState
from app.infrastructure.llm.exceptions import LLMError
from app.infrastructure.llm.prompts.retrieval import build_plan_prompt
from app.infrastructure.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


def build_understand_query_node(provider: LLMProvider) -> ConversationNode:
    async def understand_query(state: ConversationTurnState) -> ConversationTurnState:
        """Work out what this question needs before gathering anything.

        A failure here falls back to semantic recall over all time, which is what
        the previous phase did for every question. The answer gets worse, not
        absent.
        """
        message = state.get("message") or ""
        if not message:
            return {"plan": QueryPlan()}

        try:
            plan = await provider.generate_structured(build_plan_prompt(message), QueryPlan)
        except LLMError as error:
            logger.warning("query_planning_failed", extra={"error_type": type(error).__name__})
            return {
                "plan": QueryPlan(),
                "errors": [*(state.get("errors") or []), "query_planning_failed"],
            }

        logger.info(
            "query_planned",
            extra={
                "needs": [need.value for need in plan.needs],
                "range_spec": plan.time_range.value,
                "activity_count": len(plan.activities),
            },
        )

        return {"plan": plan}

    return understand_query
