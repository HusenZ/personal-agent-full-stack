import logging
import time
from collections import Counter

from app.domain.conversation.context import ConversationContext, ProfileContext
from app.domain.conversation.verification import AnswerVerification, confirm_findings
from app.graph.conversation.state import ConversationNode, ConversationTurnState
from app.infrastructure.llm.exceptions import LLMError
from app.infrastructure.llm.prompts.conversation import build_verification_prompt
from app.infrastructure.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


def build_verify_answer_node(provider: LLMProvider) -> ConversationNode:
    async def verify_answer(state: ConversationTurnState) -> ConversationTurnState:
        """Check the latest draft for statements about the user its context lacks.

        A failure here fails open: the draft is returned unverified rather than
        withheld. Verification exists to catch invented claims, and an outage
        of the checker is not evidence that a reply invented anything.
        """
        draft = state.get("reply") or ""
        context = state.get("context") or ConversationContext(
            profile=ProfileContext(), history=(), current_message=state.get("message") or ""
        )
        attempt = state.get("generation_attempts") or 0

        started = time.perf_counter()
        try:
            verification = await provider.generate_structured(
                build_verification_prompt(context, draft), AnswerVerification
            )
        except LLMError as error:
            logger.warning(
                "answer_verification_failed",
                extra={"error_type": type(error).__name__, "attempt": attempt},
            )
            return {
                "grounded": None,
                "errors": [*(state.get("errors") or []), "answer_verification_failed"],
            }

        findings = confirm_findings(draft, verification)

        # Keep the earlier draft on a tie.
        best_count = state.get("best_findings")
        if best_count is None or len(findings) < best_count:
            best_reply, best_count = draft, len(findings)
        else:
            best_reply = state.get("best_reply") or draft

        logger.info(
            "answer_verified",
            extra={
                "user_id": state.get("user_id"),
                "conversation_id": state.get("conversation_id"),
                "attempt": attempt,
                "reported": len(verification.unsupported),
                "confirmed": len(findings),
                "reasons": dict(Counter(finding.reason.value for finding in findings)),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )

        return {
            "reply": best_reply,
            "best_reply": best_reply,
            "best_findings": best_count,
            "findings": list(findings),
            "grounded": best_count == 0,
        }

    return verify_answer
