import logging

from app.domain.onboarding.context import CollectedContext, ExtractionResult, merge_extraction
from app.graph.onboarding.state import OnboardingNode, OnboardingTurnState
from app.infrastructure.llm.exceptions import LLMError
from app.infrastructure.llm.prompts.onboarding import build_extraction_prompt
from app.infrastructure.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


def build_extract_information_node(provider: LLMProvider) -> OnboardingNode:
    async def extract_information(state: OnboardingTurnState) -> OnboardingTurnState:
        """Turn the user's answer into structured evidence.

        Skipped on the opening turn, when there is no answer yet.
        """
        question = state.get("pending_question")
        answer = state.get("answer")

        if question is None or not answer:
            return {"answer_was_usable": False}

        prompt = build_extraction_prompt(question.text, answer)

        try:
            extraction = await provider.generate_structured(prompt, ExtractionResult)
        except LLMError as error:
            # A failed extraction loses this answer but the interview continues.
            logger.warning(
                "onboarding_extraction_failed", extra={"error_type": type(error).__name__}
            )
            return {
                "answer_was_usable": False,
                "errors": [*(state.get("errors") or []), "extraction_failed"],
            }

        collected = merge_extraction(state.get("collected") or CollectedContext(), extraction)

        return {
            "collected": collected,
            "answer_was_usable": extraction.answered and not extraction.is_empty(),
        }

    return extract_information
