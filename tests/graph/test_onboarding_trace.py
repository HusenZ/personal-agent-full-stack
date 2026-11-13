import uuid
from typing import Any

import pytest
from pydantic import BaseModel

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
from app.graph.onboarding.state import AskedQuestion, OnboardingTurnState
from app.infrastructure.llm.prompt import Prompt
from app.infrastructure.llm.tracing import TracingLLMProvider
from app.infrastructure.observability.tracing import RunTrace, SpanStatus, bind_trace
from tests.fakes import FakeLLMProvider, UnavailableLLMProvider

POLICY = OnboardingPolicy(confidence_threshold=0.75, min_questions=2, max_questions=5)
GOAL_QUESTION = AskedQuestion(dimension=ContextDimension.GOAL, text="What do you want?")


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


def opening_state() -> OnboardingTurnState:
    return {
        "user_id": str(uuid.uuid4()),
        "pending_question": None,
        "answer": None,
        "asked_questions": [],
        "questions_asked": 0,
        "collected": CollectedContext(),
        "errors": [],
    }


def answer_state(questions_asked: int = 1) -> OnboardingTurnState:
    state = opening_state()
    state.update(
        {
            "pending_question": GOAL_QUESTION,
            "answer": "I want to move into AI engineering, evenings only.",
            "asked_questions": [GOAL_QUESTION] * questions_asked,
            "questions_asked": questions_asked,
        }
    )
    return state


async def traced_turn(provider: Any, state: OnboardingTurnState) -> RunTrace:
    graph = build_onboarding_graph(TracingLLMProvider(provider), POLICY)
    with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
        await graph.ainvoke(state)
    return trace


def calls_by_node(trace: RunTrace) -> list[tuple[str, list[str]]]:
    return [(step.node, [call.name for call in step.model_calls]) for step in trace.steps]


class TestOnboardingTrace:
    async def test_the_opening_turn_is_traced_without_model_calls(self):
        trace = await traced_turn(FakeLLMProvider(), opening_state())

        assert calls_by_node(trace) == [
            ("extract_information", []),
            ("assess_context", []),
            ("generate_question", []),
        ]
        assert trace.other_calls == []

    async def test_an_answer_records_its_extraction_and_next_question(self):
        provider = FakeLLMProvider(extractions=[ExtractionResult()], questions=["And then?"])

        trace = await traced_turn(provider, answer_state())

        assert calls_by_node(trace) == [
            ("extract_information", ["onboarding.extraction"]),
            ("assess_context", []),
            ("generate_question", ["onboarding.question"]),
        ]

    async def test_completion_is_traced_through_build_profile(self):
        provider = FakeLLMProvider(extractions=[confident_extraction()])

        trace = await traced_turn(provider, answer_state(questions_asked=2))

        assert calls_by_node(trace) == [
            ("extract_information", ["onboarding.extraction"]),
            ("assess_context", []),
            ("build_profile", []),
        ]

    async def test_every_node_in_the_graph_is_traced(self):
        """Across the opening, a question turn and completion, all four nodes appear."""
        graph = build_onboarding_graph(FakeLLMProvider(), POLICY)
        declared = {name for name in graph.get_graph().nodes if not name.startswith("__")}

        traced: set[str] = set()
        for provider, state in (
            (FakeLLMProvider(), opening_state()),
            (FakeLLMProvider(extractions=[ExtractionResult()]), answer_state()),
            (FakeLLMProvider(extractions=[confident_extraction()]), answer_state(2)),
        ):
            trace = await traced_turn(provider, state)
            traced |= {step.node for step in trace.steps}

        assert declared == {
            "extract_information",
            "assess_context",
            "generate_question",
            "build_profile",
        }
        assert traced == declared

    async def test_a_degraded_model_call_is_recorded_on_its_step(self):
        """The nodes absorb model failures; the trace still shows where they happened."""
        trace = await traced_turn(UnavailableLLMProvider(), answer_state())

        statuses = [
            (step.node, step.status, [(c.status, c.error_type) for c in step.model_calls])
            for step in trace.steps
        ]
        assert statuses == [
            (
                "extract_information",
                SpanStatus.OK,
                [(SpanStatus.FAILED, "LLMUnavailableError")],
            ),
            ("assess_context", SpanStatus.OK, []),
            (
                "generate_question",
                SpanStatus.OK,
                [(SpanStatus.FAILED, "LLMUnavailableError")],
            ),
        ]

    async def test_steps_are_laid_out_in_time(self):
        trace = await traced_turn(FakeLLMProvider(), answer_state())

        offsets = [step.offset_ms for step in trace.steps]
        assert offsets == sorted(offsets)

    async def test_a_step_that_raises_is_marked_as_the_failure(self):
        class BrokenProvider(FakeLLMProvider):
            async def generate_structured[ModelT: BaseModel](
                self, prompt: Prompt, schema: type[ModelT]
            ) -> ModelT:
                raise ConnectionError("socket closed")

        graph = build_onboarding_graph(TracingLLMProvider(BrokenProvider()), POLICY)
        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            with pytest.raises(ConnectionError):
                await graph.ainvoke(answer_state())

        failed = trace.failed_step()
        assert failed is not None
        assert failed.node == "extract_information"
        assert failed.error_type == "ConnectionError"
        assert [step.node for step in trace.steps] == ["extract_information"]

    async def test_without_a_bound_trace_the_graph_runs_untraced(self):
        graph = build_onboarding_graph(FakeLLMProvider(), POLICY)

        result = await graph.ainvoke(opening_state())

        assert result["questions_asked"] == 1
