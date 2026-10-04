from typing import Any

from app.domain.memory.decisions import (
    DecisionKind,
    MemoryJudgment,
    MemoryVerdict,
    PairJudgment,
)
from app.domain.memory.pairing import ExistingMemory
from app.domain.memory.policy import DEFAULT_MEMORY_POLICY, MemoryPolicy
from app.domain.memory.types import (
    MemoryCandidate,
    MemoryDurability,
    MemoryExtraction,
    MemoryType,
)
from app.graph.memory.graph import build_memory_graph
from app.graph.memory.state import MemoryExtractionState
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider, UnavailableLLMProvider


def candidate(
    content: str = "I want to move into AI engineering",
    memory_type: MemoryType = MemoryType.GOAL,
    *,
    importance: float = 0.85,
    confidence: float = 0.9,
) -> MemoryCandidate:
    return MemoryCandidate(
        content=content,
        memory_type=memory_type,
        durability=MemoryDurability.LONG_TERM,
        importance=importance,
        confidence=confidence,
    )


def initial_state(**overrides: Any) -> MemoryExtractionState:
    state: MemoryExtractionState = {
        "user_id": "u1",
        "conversation_id": "c1",
        "user_message": "I have decided to move into AI engineering.",
        "assistant_reply": "That is a clear direction.",
        "existing": [],
        "errors": [],
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


class TestExtraction:
    async def test_candidates_are_extracted(self):
        provider = FakeLLMProvider(
            extractions_of_memory=[MemoryExtraction(candidates=[candidate()])]
        )
        graph = build_memory_graph(provider, FakeEmbeddingProvider(), DEFAULT_MEMORY_POLICY)

        result = await graph.ainvoke(initial_state())

        assert [item.content for item in result["candidates"]] == [
            "I want to move into AI engineering"
        ]

    async def test_the_exchange_reaches_the_prompt(self):
        provider = FakeLLMProvider(extractions_of_memory=[MemoryExtraction()])
        graph = build_memory_graph(provider, FakeEmbeddingProvider(), DEFAULT_MEMORY_POLICY)

        await graph.ainvoke(initial_state())

        prompt = provider.prompts_named("memory.extraction")[0]
        assert "move into AI engineering" in prompt.user
        assert "That is a clear direction" in prompt.user

    async def test_an_exchange_worth_nothing_costs_one_call(self):
        """Most exchanges contain nothing to remember; that case should be cheap."""
        provider = FakeLLMProvider(extractions_of_memory=[MemoryExtraction()])
        graph = build_memory_graph(provider, FakeEmbeddingProvider(), DEFAULT_MEMORY_POLICY)

        result = await graph.ainvoke(initial_state())

        assert result["decisions"] == [] if "decisions" in result else True
        assert len(provider.prompts) == 1


class TestFiltering:
    async def test_weak_candidates_are_dropped_before_any_comparison(self):
        provider = FakeLLMProvider(
            extractions_of_memory=[
                MemoryExtraction(
                    candidates=[
                        candidate(),
                        candidate("Nice weather", MemoryType.TEMPORARY_CONTEXT, importance=0.05),
                        candidate("Maybe likes jazz", MemoryType.FACT, confidence=0.1),
                    ]
                )
            ]
        )
        graph = build_memory_graph(provider, FakeEmbeddingProvider(), DEFAULT_MEMORY_POLICY)

        result = await graph.ainvoke(initial_state())

        assert len(result["admitted"]) == 1
        assert result["rejected_count"] == 2

    async def test_filtering_happens_before_the_second_model_call(self):
        """A candidate that fails the bar should never cost a judgement call."""
        provider = FakeLLMProvider(
            extractions_of_memory=[
                MemoryExtraction(candidates=[candidate(importance=0.01, confidence=0.02)])
            ]
        )
        graph = build_memory_graph(provider, FakeEmbeddingProvider(), DEFAULT_MEMORY_POLICY)

        await graph.ainvoke(initial_state())

        assert provider.prompts_named("memory.judgement") == []


class TestConflictResolution:
    async def test_nothing_existing_means_no_judgement_call(self):
        provider = FakeLLMProvider(
            extractions_of_memory=[MemoryExtraction(candidates=[candidate()])]
        )
        graph = build_memory_graph(provider, FakeEmbeddingProvider(), DEFAULT_MEMORY_POLICY)

        result = await graph.ainvoke(initial_state())

        assert provider.prompts_named("memory.judgement") == []
        assert [decision.kind for decision in result["decisions"]] == [DecisionKind.CREATE]

    async def test_a_restatement_is_resolved_without_a_judgement_call(self):
        provider = FakeLLMProvider(
            extractions_of_memory=[MemoryExtraction(candidates=[candidate()])]
        )
        graph = build_memory_graph(provider, FakeEmbeddingProvider(), DEFAULT_MEMORY_POLICY)

        result = await graph.ainvoke(
            initial_state(
                existing=[
                    ExistingMemory(
                        id="m1",
                        content="I want to move into AI engineering",
                        memory_type=MemoryType.GOAL,
                        importance=0.7,
                        confidence=0.8,
                    )
                ]
            )
        )

        assert provider.prompts_named("memory.judgement") == []
        assert [decision.kind for decision in result["decisions"]] == [DecisionKind.REINFORCE]

    async def test_a_contradiction_supersedes(self):
        provider = FakeLLMProvider(
            extractions_of_memory=[MemoryExtraction(candidates=[candidate()])],
            judgments=[
                MemoryJudgment(
                    judgments=[PairJudgment(memory_id="m1", verdict=MemoryVerdict.CONTRADICTS)]
                )
            ],
        )
        graph = build_memory_graph(provider, FakeEmbeddingProvider(), DEFAULT_MEMORY_POLICY)

        result = await graph.ainvoke(
            initial_state(
                existing=[
                    ExistingMemory(
                        id="m1",
                        content="I have decided to stay in mobile development",
                        memory_type=MemoryType.GOAL,
                        importance=0.8,
                        confidence=0.9,
                    )
                ]
            )
        )

        decision = result["decisions"][0]
        assert decision.kind is DecisionKind.SUPERSEDE
        assert [memory.id for memory in decision.supersedes] == ["m1"]
        assert result["judged_pairs"] == 1

    async def test_only_the_narrowed_memories_reach_the_model(self):
        """Extraction must not mean showing a model the user's whole history."""
        provider = FakeLLMProvider(
            extractions_of_memory=[MemoryExtraction(candidates=[candidate()])],
            judgments=[MemoryJudgment()],
        )
        graph = build_memory_graph(
            provider, FakeEmbeddingProvider(), MemoryPolicy(max_judged_pairs=2)
        )

        unrelated = [
            ExistingMemory(
                id=f"u{index}",
                content=f"Unrelated fact number {index} about cooking",
                memory_type=MemoryType.FACT,
                importance=0.5,
                confidence=0.5,
            )
            for index in range(20)
        ]
        result = await graph.ainvoke(initial_state(existing=unrelated))

        assert result["judged_pairs"] == 0
        assert provider.prompts_named("memory.judgement") == []


class TestProviderFailure:
    async def test_a_failed_extraction_remembers_nothing_rather_than_raising(self):
        graph = build_memory_graph(
            UnavailableLLMProvider(), FakeEmbeddingProvider(), DEFAULT_MEMORY_POLICY
        )

        result = await graph.ainvoke(initial_state())

        assert result["candidates"] == []
        assert "extraction_failed" in result["errors"]

    async def test_a_failed_judgement_creates_rather_than_supersedes(self):
        class FailsJudgementOnly(FakeLLMProvider):
            async def generate_structured(self, prompt: Any, schema: Any) -> Any:
                if prompt.name == "memory.judgement":
                    raise UnavailableLLMProvider()._failure  # type: ignore[misc]
                return await super().generate_structured(prompt, schema)

        provider = FailsJudgementOnly(
            extractions_of_memory=[MemoryExtraction(candidates=[candidate()])]
        )
        graph = build_memory_graph(provider, FakeEmbeddingProvider(), DEFAULT_MEMORY_POLICY)

        result = await graph.ainvoke(
            initial_state(
                existing=[
                    ExistingMemory(
                        id="m1",
                        content="I have decided to stay in mobile development",
                        memory_type=MemoryType.GOAL,
                        importance=0.8,
                        confidence=0.9,
                    )
                ]
            )
        )

        assert [decision.kind for decision in result["decisions"]] == [DecisionKind.CREATE]
