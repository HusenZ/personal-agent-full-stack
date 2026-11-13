import uuid
from typing import Any

import pytest
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from app.domain.analysis.claims import AnalysisClaim, AnalysisResult, AnalysisSection, ClaimType
from app.domain.analysis.evidence import RawEvidence
from app.domain.analysis.policy import AnalysisPolicy
from app.graph.analysis.graph import build_analysis_graph, max_steps
from app.graph.analysis.state import AnalysisState
from app.infrastructure.llm.prompt import Prompt
from app.infrastructure.llm.tracing import TracingLLMProvider
from app.infrastructure.observability.tracing import RunTrace, SpanStatus, bind_trace
from tests.fakes import FakeLLMProvider, UnavailableLLMProvider

POLICY = AnalysisPolicy(max_generation_attempts=2, min_claims=3)

RAW = RawEvidence(
    goals=("Move into AI engineering", "Ship a backend"),
    constraints=("Only evenings free",),
    preferences=(("communication_style", "direct"),),
    observations=("Flutter developer today",),
)


def claim(content: str, evidence_ids: list[str] | None = None) -> AnalysisClaim:
    return AnalysisClaim(
        section=AnalysisSection.GOALS,
        claim_type=ClaimType.FACT,
        content=content,
        confidence=0.9,
        evidence_ids=evidence_ids if evidence_ids is not None else ["goal:1"],
    )


GOOD = AnalysisResult(
    claims=[
        claim("Wants to move into AI engineering"),
        claim("Evenings are the only free time", ["constraint:1"]),
        claim("Prefers direct communication", ["preference:communication_style"]),
    ]
)
HALLUCINATED = AnalysisResult(claims=[claim("Cites nothing real", ["goal:99"])])


def initial_state() -> AnalysisState:
    return {"user_id": str(uuid.uuid4()), "raw_evidence": RAW, "attempt": 0, "errors": []}


async def traced_run(provider: Any) -> RunTrace:
    graph = build_analysis_graph(TracingLLMProvider(provider), POLICY)
    with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
        await graph.ainvoke(initial_state())
    return trace


def calls_by_node(trace: RunTrace) -> list[tuple[str, list[str]]]:
    return [(step.node, [call.name for call in step.model_calls]) for step in trace.steps]


class TestAnalysisTrace:
    async def test_every_step_is_recorded_in_order_with_its_model_calls(self):
        trace = await traced_run(FakeLLMProvider(analyses=[GOOD]))

        assert calls_by_node(trace) == [
            ("build_evidence", []),
            ("generate_analysis", ["analysis.initial"]),
            ("verify_grounding", []),
        ]
        assert trace.other_calls == []

    async def test_a_retry_appears_as_its_own_steps(self):
        trace = await traced_run(FakeLLMProvider(analyses=[HALLUCINATED, GOOD]))

        assert [step.node for step in trace.steps] == [
            "build_evidence",
            "generate_analysis",
            "verify_grounding",
            "generate_analysis",
            "verify_grounding",
        ]

    async def test_the_retry_budget_bounds_the_trace(self):
        """A model that never grounds a claim stops at the attempt budget."""
        trace = await traced_run(FakeLLMProvider(analyses=[HALLUCINATED]))

        generations = [step for step in trace.steps if step.node == "generate_analysis"]
        assert len(generations) == POLICY.max_generation_attempts

    async def test_every_node_in_the_graph_is_traced(self):
        graph = build_analysis_graph(FakeLLMProvider(), POLICY)
        declared = {name for name in graph.get_graph().nodes if not name.startswith("__")}

        trace = await traced_run(FakeLLMProvider(analyses=[GOOD]))

        assert declared == {"build_evidence", "generate_analysis", "verify_grounding"}
        assert {step.node for step in trace.steps} == declared

    async def test_a_failed_generation_is_recorded_on_its_step(self):
        """The node absorbs the failure and the budget retries; both calls are visible."""
        trace = await traced_run(UnavailableLLMProvider())

        generations = [step for step in trace.steps if step.node == "generate_analysis"]
        assert [step.status for step in generations] == [SpanStatus.OK, SpanStatus.OK]
        assert [
            (call.status, call.error_type) for step in generations for call in step.model_calls
        ] == [(SpanStatus.FAILED, "LLMUnavailableError")] * 2

    async def test_steps_are_laid_out_in_time(self):
        trace = await traced_run(FakeLLMProvider(analyses=[HALLUCINATED, GOOD]))

        offsets = [step.offset_ms for step in trace.steps]
        assert offsets == sorted(offsets)

    async def test_a_step_that_raises_is_marked_as_the_failure(self):
        class BrokenProvider(FakeLLMProvider):
            async def generate_structured[ModelT: BaseModel](
                self, prompt: Prompt, schema: type[ModelT]
            ) -> ModelT:
                raise ConnectionError("socket closed")

        graph = build_analysis_graph(TracingLLMProvider(BrokenProvider()), POLICY)
        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            with pytest.raises(ConnectionError):
                await graph.ainvoke(initial_state())

        failed = trace.failed_step()
        assert failed is not None
        assert failed.node == "generate_analysis"
        assert failed.error_type == "ConnectionError"
        assert [step.status for step in trace.steps] == [SpanStatus.OK, SpanStatus.FAILED]


class TestStepBound:
    """The graph is compiled with the exact worst case, so a trace is bounded too."""

    @pytest.mark.parametrize("attempts", [1, 2, 3])
    async def test_a_model_that_never_grounds_runs_to_the_budget_and_no_further(
        self, attempts: int
    ):
        policy = AnalysisPolicy(max_generation_attempts=attempts, min_claims=3)
        graph = build_analysis_graph(
            TracingLLMProvider(FakeLLMProvider(analyses=[HALLUCINATED])), policy
        )

        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            await graph.ainvoke(initial_state())

        assert len(trace.steps) == max_steps(policy) == 1 + 2 * attempts

    @pytest.mark.parametrize("attempts", [1, 2, 3])
    async def test_the_limit_has_no_slack(self, attempts: int):
        policy = AnalysisPolicy(max_generation_attempts=attempts, min_claims=3)
        graph = build_analysis_graph(FakeLLMProvider(analyses=[HALLUCINATED]), policy)
        assert graph.config is not None
        tighter = graph.with_config(recursion_limit=graph.config["recursion_limit"] - 1)

        with pytest.raises(GraphRecursionError):
            await tighter.ainvoke(initial_state())
