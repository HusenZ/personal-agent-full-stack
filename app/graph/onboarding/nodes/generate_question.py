import logging

from pydantic import BaseModel, Field

from app.domain.onboarding.assessment import assess_coverage, select_next_dimension
from app.domain.onboarding.context import CollectedContext
from app.graph.onboarding.state import AskedQuestion, OnboardingNode, OnboardingTurnState
from app.infrastructure.llm.exceptions import LLMError
from app.infrastructure.llm.prompts.onboarding import OPENING_QUESTION, build_question_prompt
from app.infrastructure.llm.provider import LLMProvider

logger = logging.getLogger(__name__)

# Fallback question when the model is unavailable.
_FALLBACK_QUESTIONS = {
    "goal": "What would you most like to achieve over the next few months?",
    "context": "Where are you starting from with that right now?",
    "constraint": "What gets in the way of making progress on it?",
    "preference": "How do you prefer to work when you are making real progress?",
}


class GeneratedQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=500)


def build_generate_question_node(provider: LLMProvider) -> OnboardingNode:
    async def generate_question(state: OnboardingTurnState) -> OnboardingTurnState:
        collected = state.get("collected") or CollectedContext()
        coverage = assess_coverage(collected)
        dimension = select_next_dimension(coverage)
        asked = state.get("asked_questions") or []

        # First question is fixed; nothing is known yet for a model to adapt to.
        if not state.get("questions_asked"):
            opening = AskedQuestion(dimension=dimension, text=OPENING_QUESTION)
            return {
                "next_question": opening,
                "asked_questions": [*asked, opening],
                "questions_asked": 1,
            }

        prompt = build_question_prompt(
            dimension=dimension,
            already_asked=[question.text for question in asked],
            context_summary=_summarise(collected),
        )

        try:
            generated = await provider.generate_structured(prompt, GeneratedQuestion)
            text = generated.question.strip()
        except LLMError as error:
            logger.warning(
                "onboarding_question_generation_failed",
                extra={"error_type": type(error).__name__, "dimension": dimension.value},
            )
            text = _FALLBACK_QUESTIONS[dimension.value]

        question = AskedQuestion(dimension=dimension, text=text)

        return {
            "next_question": question,
            "asked_questions": [*asked, question],
            "questions_asked": (state.get("questions_asked") or 0) + 1,
        }

    return generate_question


def _summarise(collected: CollectedContext) -> str:
    """Render what is known as compact lines for the prompt.

    Deliberately not the full structure: the question generator needs to avoid
    repeating itself, not to re-derive every confidence score.
    """
    lines: list[str] = []
    lines += [f"Goal: {goal.name} (priority {goal.priority})" for goal in collected.goals]
    lines += [f"Constraint: {item.description}" for item in collected.constraints]
    lines += [f"Prefers {item.key}: {item.value}" for item in collected.preferences]
    lines += [f"Context: {item.content}" for item in collected.observations]
    return "\n".join(lines)
