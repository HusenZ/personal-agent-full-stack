from typing import Any

import pytest

from app.domain.onboarding.context import (
    CollectedContext,
    ExtractedConstraint,
    ExtractedGoal,
    ExtractedObservation,
    ExtractedPreference,
    ExtractionResult,
)
from app.domain.onboarding.dimensions import ContextDimension
from app.domain.onboarding.policy import OnboardingPolicy
from app.graph.onboarding.graph import build_onboarding_graph
from app.graph.onboarding.nodes.build_profile import MIN_CONFIDENCE_FOR_PROFILE
from app.graph.onboarding.state import AskedQuestion, OnboardingTurnState
from app.infrastructure.llm.prompts.onboarding import OPENING_QUESTION
from tests.fakes import FakeLLMProvider, UnavailableLLMProvider

POLICY = OnboardingPolicy(confidence_threshold=0.75, min_questions=2, max_questions=5)


def initial_state(**overrides: Any) -> OnboardingTurnState:
    state: OnboardingTurnState = {
        "user_id": "user-1",
        "pending_question": None,
        "answer": None,
        "asked_questions": [],
        "questions_asked": 0,
        "collected": CollectedContext(),
        "errors": [],
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def confident_extraction() -> ExtractionResult:
    return ExtractionResult(
        goals=[
            ExtractedGoal(name="Move into AI engineering", priority="high", confidence=0.95),
            ExtractedGoal(name="Ship a portfolio backend", priority="medium", confidence=0.9),
        ],
        constraints=[ExtractedConstraint(description="Only evenings free", confidence=0.9)],
        preferences=[
            ExtractedPreference(key="communication_style", value="direct", confidence=0.9)
        ],
        observations=[
            ExtractedObservation(content="Currently a Flutter developer", confidence=0.9),
            ExtractedObservation(content="Has shipped production apps", confidence=0.9),
        ],
    )


class TestOpeningTurn:
    async def test_the_first_turn_asks_the_fixed_opening_question(self):
        graph = build_onboarding_graph(FakeLLMProvider(), POLICY)

        result = await graph.ainvoke(initial_state())

        assert result["next_question"].text == OPENING_QUESTION
        assert result["questions_asked"] == 1
        assert not result["is_complete"]

    async def test_the_opening_question_costs_no_model_call(self):
        """Nothing is known yet for a model to adapt to, so a call is pure latency."""
        provider = FakeLLMProvider()
        graph = build_onboarding_graph(provider, POLICY)

        await graph.ainvoke(initial_state())

        assert provider.prompts == []

    async def test_no_extraction_runs_without_an_answer(self):
        graph = build_onboarding_graph(FakeLLMProvider(), POLICY)

        result = await graph.ainvoke(initial_state())

        assert result["answer_was_usable"] is False
        assert result["collected"] == CollectedContext()


class TestExtractionTurn:
    async def test_an_answer_becomes_collected_evidence(self):
        provider = FakeLLMProvider(extractions=[confident_extraction()])
        graph = build_onboarding_graph(provider, POLICY)

        result = await graph.ainvoke(
            initial_state(
                pending_question=AskedQuestion(
                    dimension=ContextDimension.GOAL, text=OPENING_QUESTION
                ),
                answer="I want to move into AI engineering.",
                questions_asked=1,
            )
        )

        assert [goal.name for goal in result["collected"].goals] == [
            "Move into AI engineering",
            "Ship a portfolio backend",
        ]

    async def test_evidence_accumulates_across_turns(self):
        provider = FakeLLMProvider(
            extractions=[
                ExtractionResult(goals=[ExtractedGoal(name="Second goal", confidence=0.8)])
            ]
        )
        graph = build_onboarding_graph(provider, POLICY)

        result = await graph.ainvoke(
            initial_state(
                pending_question=AskedQuestion(dimension=ContextDimension.GOAL, text="q"),
                answer="Also this.",
                questions_asked=1,
                collected=CollectedContext(
                    goals=[ExtractedGoal(name="First goal", confidence=0.7)]
                ),
            )
        )

        assert {goal.name for goal in result["collected"].goals} == {"First goal", "Second goal"}

    async def test_a_skipped_answer_is_recorded_as_unusable(self):
        provider = FakeLLMProvider(extractions=[ExtractionResult(answered=False)])
        graph = build_onboarding_graph(provider, POLICY)

        result = await graph.ainvoke(
            initial_state(
                pending_question=AskedQuestion(dimension=ContextDimension.GOAL, text="q"),
                answer="I'd rather not say.",
                questions_asked=1,
            )
        )

        assert result["answer_was_usable"] is False
        assert not result["is_complete"]
        assert result["next_question"] is not None


class TestRouting:
    async def test_insufficient_context_routes_to_a_new_question(self):
        provider = FakeLLMProvider(
            extractions=[ExtractionResult()], questions=["What is getting in the way?"]
        )
        graph = build_onboarding_graph(provider, POLICY)

        result = await graph.ainvoke(
            initial_state(
                pending_question=AskedQuestion(dimension=ContextDimension.GOAL, text="q"),
                answer="Not sure.",
                questions_asked=2,
            )
        )

        assert not result["is_complete"]
        assert result["next_question"].text == "What is getting in the way?"
        assert result.get("profile_draft") is None

    async def test_sufficient_context_routes_to_building_a_profile(self):
        provider = FakeLLMProvider(extractions=[confident_extraction()])
        graph = build_onboarding_graph(provider, POLICY)

        result = await graph.ainvoke(
            initial_state(
                pending_question=AskedQuestion(dimension=ContextDimension.GOAL, text="q"),
                answer="A very informative answer.",
                questions_asked=2,
            )
        )

        assert result["is_complete"]
        assert result["completion_reason"] == "confidence_threshold_met"
        assert result["next_question"] is None
        assert result["profile_draft"] is not None

    async def test_the_question_ceiling_ends_the_interview(self):
        """The interview must terminate even when nothing useful is ever extracted."""
        provider = FakeLLMProvider(extractions=[ExtractionResult()])
        graph = build_onboarding_graph(provider, POLICY)

        result = await graph.ainvoke(
            initial_state(
                pending_question=AskedQuestion(dimension=ContextDimension.GOAL, text="q"),
                answer="No comment.",
                questions_asked=POLICY.max_questions,
            )
        )

        assert result["is_complete"]
        assert result["completion_reason"] == "question_limit_reached"

    async def test_asked_questions_accumulate_without_repeating(self):
        provider = FakeLLMProvider(extractions=[ExtractionResult()], questions=["A new question"])
        graph = build_onboarding_graph(provider, POLICY)
        previous = AskedQuestion(dimension=ContextDimension.GOAL, text=OPENING_QUESTION)

        result = await graph.ainvoke(
            initial_state(
                pending_question=previous,
                answer="Something",
                questions_asked=1,
                asked_questions=[previous],
            )
        )

        assert [question.text for question in result["asked_questions"]] == [
            OPENING_QUESTION,
            "A new question",
        ]

    async def test_previously_asked_questions_are_given_to_the_generator(self):
        provider = FakeLLMProvider(extractions=[ExtractionResult()], questions=["Next"])
        graph = build_onboarding_graph(provider, POLICY)
        previous = AskedQuestion(dimension=ContextDimension.GOAL, text="Do not repeat me")

        await graph.ainvoke(
            initial_state(
                pending_question=previous,
                answer="Something",
                questions_asked=1,
                asked_questions=[previous],
            )
        )

        question_prompt = provider.prompts_named("onboarding.question")[0]
        assert "Do not repeat me" in question_prompt.user


class TestProviderFailure:
    async def test_a_failed_extraction_does_not_end_the_interview(self):
        """Losing one answer's evidence is recoverable; failing the request is not."""
        graph = build_onboarding_graph(UnavailableLLMProvider(), POLICY)

        result = await graph.ainvoke(
            initial_state(
                pending_question=AskedQuestion(dimension=ContextDimension.GOAL, text="q"),
                answer="An answer the model could not process.",
                questions_asked=1,
            )
        )

        assert "extraction_failed" in result["errors"]
        assert result["next_question"] is not None

    async def test_a_failed_generation_falls_back_to_a_usable_question(self):
        graph = build_onboarding_graph(UnavailableLLMProvider(), POLICY)

        result = await graph.ainvoke(
            initial_state(
                pending_question=AskedQuestion(dimension=ContextDimension.GOAL, text="q"),
                answer="An answer",
                questions_asked=1,
            )
        )

        assert result["next_question"].text
        assert result["questions_asked"] == 2


class TestProfileDraft:
    async def _draft(self, extraction: ExtractionResult):
        provider = FakeLLMProvider(extractions=[extraction])
        graph = build_onboarding_graph(provider, POLICY)
        result = await graph.ainvoke(
            initial_state(
                pending_question=AskedQuestion(dimension=ContextDimension.GOAL, text="q"),
                answer="answer",
                questions_asked=POLICY.max_questions,
            )
        )
        return result["profile_draft"]

    async def test_the_draft_carries_goals_constraints_and_preferences(self):
        draft = await self._draft(confident_extraction())

        assert ("Move into AI engineering", "high") in draft.goals
        assert "Only evenings free" in draft.constraints
        assert draft.preferences["communication_style"] == "direct"

    async def test_goals_are_ordered_by_confidence(self):
        draft = await self._draft(confident_extraction())

        assert draft.goals[0][0] == "Move into AI engineering"

    async def test_low_confidence_evidence_is_left_out(self):
        """A guess should not become something the assistant later asserts."""
        below = MIN_CONFIDENCE_FOR_PROFILE - 0.1
        draft = await self._draft(
            ExtractionResult(
                goals=[ExtractedGoal(name="Barely implied", confidence=below)],
                constraints=[ExtractedConstraint(description="Vague", confidence=below)],
            )
        )

        assert draft.goals == []
        assert draft.constraints == []

    @pytest.mark.parametrize("confidence", [MIN_CONFIDENCE_FOR_PROFILE, 1.0])
    async def test_evidence_at_or_above_the_floor_is_kept(self, confidence: float):
        draft = await self._draft(
            ExtractionResult(goals=[ExtractedGoal(name="Kept", confidence=confidence)])
        )

        assert [name for name, _ in draft.goals] == ["Kept"]
