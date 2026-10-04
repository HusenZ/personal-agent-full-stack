from typing import Any

import pytest

from app.domain.analysis.claims import (
    AnalysisClaim,
    AnalysisResult,
    AnalysisSection,
    ClaimType,
)
from app.domain.analysis.evidence import RawEvidence
from app.domain.analysis.policy import AnalysisPolicy
from app.graph.analysis.graph import build_analysis_graph
from app.graph.analysis.state import AnalysisState
from tests.fakes import FakeLLMProvider, UnavailableLLMProvider

POLICY = AnalysisPolicy(max_generation_attempts=2, min_claims=3)

RAW = RawEvidence(
    goals=("Move into AI engineering", "Ship a backend"),
    constraints=("Only evenings free",),
    preferences=(("communication_style", "direct"),),
    observations=("Flutter developer today",),
)


def claim(
    content: str,
    *,
    claim_type: ClaimType = ClaimType.FACT,
    confidence: float = 0.9,
    evidence_ids: list[str] | None = None,
    section: AnalysisSection = AnalysisSection.GOALS,
) -> AnalysisClaim:
    return AnalysisClaim(
        section=section,
        claim_type=claim_type,
        content=content,
        confidence=confidence,
        evidence_ids=evidence_ids if evidence_ids is not None else ["goal:1"],
    )


def good_analysis() -> AnalysisResult:
    return AnalysisResult(
        claims=[
            claim("Wants to move into AI engineering"),
            claim("Evenings are the only free time", evidence_ids=["constraint:1"]),
            claim(
                "Limited time is the main risk to the goal",
                claim_type=ClaimType.INFERENCE,
                confidence=0.6,
                evidence_ids=["constraint:1", "goal:1"],
                section=AnalysisSection.BOTTLENECKS,
            ),
        ]
    )


def hallucinated_analysis() -> AnalysisResult:
    return AnalysisResult(
        claims=[claim("Cites evidence that does not exist", evidence_ids=["goal:99"])]
    )


def initial_state(**overrides: Any) -> AnalysisState:
    state: AnalysisState = {"user_id": "u1", "raw_evidence": RAW, "attempt": 0, "errors": []}
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


class TestEvidence:
    async def test_evidence_is_assembled_before_generation(self):
        provider = FakeLLMProvider(analyses=[good_analysis()])
        graph = build_analysis_graph(provider, POLICY)

        result = await graph.ainvoke(initial_state())

        assert "goal:1" in result["evidence"].ids
        assert "observation:1" in result["evidence"].ids

    async def test_the_evidence_reaches_the_prompt(self):
        provider = FakeLLMProvider(analyses=[good_analysis()])
        graph = build_analysis_graph(provider, POLICY)

        await graph.ainvoke(initial_state())

        prompt = provider.prompts_named("analysis.initial")[0]
        assert "[goal:1]" in prompt.user
        assert "Move into AI engineering" in prompt.user


class TestHappyPath:
    async def test_a_grounded_analysis_is_accepted_on_the_first_attempt(self):
        provider = FakeLLMProvider(analyses=[good_analysis()])
        graph = build_analysis_graph(provider, POLICY)

        result = await graph.ainvoke(initial_state())

        assert result["attempt"] == 1
        assert len(result["claims"]) == 3
        assert result["degraded"] is False
        assert result["rejected"] == []

    async def test_confidence_summarises_the_surviving_claims(self):
        provider = FakeLLMProvider(analyses=[good_analysis()])
        graph = build_analysis_graph(provider, POLICY)

        result = await graph.ainvoke(initial_state())

        assert 0.0 < result["overall_confidence"] <= 1.0


class TestRecovery:
    async def test_a_hallucinated_citation_triggers_a_retry(self):
        provider = FakeLLMProvider(analyses=[hallucinated_analysis(), good_analysis()])
        graph = build_analysis_graph(provider, POLICY)

        result = await graph.ainvoke(initial_state())

        assert result["attempt"] == 2
        assert len(result["claims"]) == 3
        assert result["degraded"] is False

    async def test_the_retry_is_told_what_was_wrong(self):
        """Rerolling the same prompt would just be hoping for a different result."""
        provider = FakeLLMProvider(analyses=[hallucinated_analysis(), good_analysis()])
        graph = build_analysis_graph(provider, POLICY)

        await graph.ainvoke(initial_state())

        retry_prompt = provider.prompts_named("analysis.initial")[1]
        assert "cite only listed ids" in retry_prompt.user

    async def test_the_better_attempt_is_kept(self):
        """A weaker retry must not lose an analysis the first attempt produced."""
        provider = FakeLLMProvider(analyses=[good_analysis(), AnalysisResult(claims=[])])
        policy = AnalysisPolicy(max_generation_attempts=2, min_claims=10)
        graph = build_analysis_graph(provider, policy)

        result = await graph.ainvoke(initial_state())

        assert len(result["claims"]) == 3


class TestBoundedRetry:
    async def test_retries_stop_at_the_attempt_ceiling(self):
        """Without this bound a model that always hallucinates would loop forever."""
        provider = FakeLLMProvider(analyses=[hallucinated_analysis()])
        graph = build_analysis_graph(provider, POLICY)

        result = await graph.ainvoke(initial_state())

        assert result["attempt"] == POLICY.max_generation_attempts
        assert len(provider.prompts_named("analysis.initial")) == 2

    async def test_an_exhausted_budget_is_reported_as_degraded(self):
        provider = FakeLLMProvider(analyses=[hallucinated_analysis()])
        graph = build_analysis_graph(provider, POLICY)

        result = await graph.ainvoke(initial_state())

        assert result["degraded"] is True
        assert result["claims"] == []

    @pytest.mark.parametrize("attempts", [1, 3])
    async def test_the_attempt_budget_is_honoured(self, attempts: int):
        provider = FakeLLMProvider(analyses=[hallucinated_analysis()])
        graph = build_analysis_graph(
            provider, AnalysisPolicy(max_generation_attempts=attempts, min_claims=3)
        )

        result = await graph.ainvoke(initial_state())

        assert result["attempt"] == attempts
        assert len(provider.prompts_named("analysis.initial")) == attempts


class TestProviderFailure:
    async def test_an_unavailable_provider_ends_degraded_rather_than_raising(self):
        graph = build_analysis_graph(UnavailableLLMProvider(), POLICY)

        result = await graph.ainvoke(initial_state())

        assert result["degraded"] is True
        assert result["claims"] == []
        assert "generation_failed" in result["errors"]

    async def test_provider_failures_still_respect_the_attempt_ceiling(self):
        graph = build_analysis_graph(UnavailableLLMProvider(), POLICY)

        result = await graph.ainvoke(initial_state())

        assert result["attempt"] == POLICY.max_generation_attempts
