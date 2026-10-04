import logging

from app.domain.conversation.context import ConversationContext, ProfileContext
from app.graph.conversation.state import ConversationNode, ConversationTurnState
from app.infrastructure.llm.exceptions import LLMError
from app.infrastructure.llm.prompts.conversation import build_reply_prompt
from app.infrastructure.llm.provider import LLMProvider

logger = logging.getLogger(__name__)

# Shown when the model is unreachable; deliberately says nothing about the user.
FALLBACK_REPLY = "I could not generate a reply just now. Please try sending that again in a moment."


def build_generate_reply_node(provider: LLMProvider) -> ConversationNode:
    async def generate_reply(state: ConversationTurnState) -> ConversationTurnState:
        """Draft a reply, avoiding whatever verification rejected last time.

        The attempt is counted before the call, so a failing provider still
        moves the verification loop towards its bound.
        """
        context = state.get("context") or ConversationContext(
            profile=ProfileContext(), history=(), current_message=state.get("message") or ""
        )
        attempt = (state.get("generation_attempts") or 0) + 1
        rejected = tuple(state.get("findings") or ())

        try:
            reply = await provider.generate_text(build_reply_prompt(context, rejected=rejected))
        except LLMError as error:
            logger.warning(
                "conversation_reply_failed",
                extra={"error_type": type(error).__name__, "attempt": attempt},
            )
            errors = [*(state.get("errors") or []), "reply_generation_failed"]

            best = state.get("best_reply")
            if best:
                # Prefer an earlier flagged draft over the generic fallback.
                return {
                    "reply": best,
                    "generation_attempts": attempt,
                    "draft_failed": True,
                    "errors": errors,
                }

            return {
                "reply": FALLBACK_REPLY,
                "generation_attempts": attempt,
                "draft_failed": True,
                "degraded": True,
                "grounded": None,
                "errors": errors,
            }

        return {
            "reply": reply,
            "generation_attempts": attempt,
            "draft_failed": False,
            "degraded": False,
        }

    return generate_reply
