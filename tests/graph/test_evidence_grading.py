import uuid
from typing import Any

import pytest
from pydantic import BaseModel

from app.domain.conversation.context import DEFAULT_CONTEXT_BUDGET
from app.domain.memory.types import MemoryType
from app.domain.retrieval.grading import EvidenceGrade, EvidencePolicy
from app.domain.retrieval.plan import QueryPlan, RetrievalNeed
from app.graph.conversation.graph import build_conversation_graph
from app.graph.conversation.nodes.retry_retrieval import build_retry_retrieval_node
from app.infrastructure.llm.exceptions import LLMInvalidOutputError, LLMUnavailableError
from app.infrastructure.llm.prompt import Prompt
from app.retrieval.types import RetrievalSource, RetrievedMemory
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider

USER_ID = str(uuid.uuid4())
QUESTION = "How is my dog getting on?"

HOMEWORK = "I am getting on with my homework"
DOG = "My dog is a labrador called Rex"


def retrieved(content: str, relevance: float = 0.5) -> RetrievedMemory:
    # Content-derived ids so the same memory matches across searches.
    return RetrievedMemory(
        id=uuid.uuid5(uuid.NAMESPACE_URL, content),
        content=content,
        memory_type=MemoryType.FACT,
        importance=0.5,
        relevance=relevance,
        sources=(RetrievalSource.KEYWORD,),
    )


class ScriptedRetriever:
    """Returns fixed memories per query, and records every search it ran."""

    def __init__(self, results: dict[str, list[RetrievedMemory]]) -> None:
        self.results = results
        self.queries: list[str] = []
        self.embeddings: list[list[float] | None] = []

    async def retrieve(
        self,
        user_id: Any,
        query_text: str,
        query_embedding: list[float] | None,
        **kwargs: Any,
    ) -> list[RetrievedMemory]:
        self.queries.append(query_text)
        self.embeddings.append(query_embedding)
        return list(self.results.get(query_text, []))


class GradingFailsProvider(FakeLLMProvider):
    """Fails only the grading call, so the rest of the turn can be observed."""

    async def generate_structured[ModelT: BaseModel](
        self, prompt: Prompt, schema: type[ModelT]
    ) -> ModelT:
        if schema is EvidenceGrade:
            self.prompts.append(prompt)
            raise LLMInvalidOutputError("grader returned nonsense")
        return await super().generate_structured(prompt, schema)


def insufficient(query: str, *, irrelevant: list[int] | None = None) -> EvidenceGrade:
    return EvidenceGrade(irrelevant=irrelevant or [], sufficient=False, search_query=query)


async def run_turn(
    provider: FakeLLMProvider,
    retriever: ScriptedRetriever,
    policy: EvidencePolicy | None = None,
    embeddings: FakeEmbeddingProvider | None = None,
    message: str = QUESTION,
) -> dict[str, Any]:
    graph = build_conversation_graph(
        provider,
        DEFAULT_CONTEXT_BUDGET,
        embeddings or FakeEmbeddingProvider(),
        retriever,
        evidence_policy=policy or EvidencePolicy(),
    )
    result: dict[str, Any] = await graph.ainvoke(
        {"user_id": USER_ID, "conversation_id": str(uuid.uuid4()), "message": message, "errors": []}
    )
    return result


def reply_prompt(provider: FakeLLMProvider) -> str:
    return provider.prompts_named("conversation.reply")[-1].user


class TestSufficientEvidence:
    async def test_sufficient_evidence_is_used_without_searching_again(self):
        provider = FakeLLMProvider(grades=[EvidenceGrade(sufficient=True)])
        retriever = ScriptedRetriever({QUESTION: [retrieved(DOG)]})

        result = await run_turn(provider, retriever)

        assert retriever.queries == [QUESTION]
        assert len(provider.prompts_named("retrieval.grade")) == 1
        assert result["retrieval_attempts"] == 1
        assert DOG in reply_prompt(provider)
        assert "did not find everything" not in reply_prompt(provider)

    async def test_memories_judged_irrelevant_never_reach_the_reply(self):
        provider = FakeLLMProvider(grades=[EvidenceGrade(irrelevant=[1], sufficient=True)])
        retriever = ScriptedRetriever({QUESTION: [retrieved(HOMEWORK, 0.9), retrieved(DOG)]})

        await run_turn(provider, retriever)

        assert HOMEWORK not in reply_prompt(provider)
        assert DOG in reply_prompt(provider)


class TestRecovery:
    async def test_a_rewritten_search_recovers_what_the_first_one_missed(self):
        provider = FakeLLMProvider(
            grades=[insufficient("dog Rex", irrelevant=[1]), EvidenceGrade(sufficient=True)]
        )
        retriever = ScriptedRetriever(
            {QUESTION: [retrieved(HOMEWORK)], "dog Rex": [retrieved(DOG)]}
        )

        result = await run_turn(provider, retriever)

        assert retriever.queries == [QUESTION, "dog Rex"]
        assert result["searches"] == [QUESTION, "dog Rex"]
        assert DOG in reply_prompt(provider)
        assert HOMEWORK not in reply_prompt(provider)
        assert "did not find everything" not in reply_prompt(provider)

    async def test_the_rewritten_search_is_embedded_rather_than_the_message(self):
        embeddings = FakeEmbeddingProvider()
        provider = FakeLLMProvider(grades=[insufficient("dog Rex"), EvidenceGrade(sufficient=True)])
        retriever = ScriptedRetriever({QUESTION: [retrieved(HOMEWORK)]})

        await run_turn(provider, retriever, embeddings=embeddings)

        assert embeddings.embedded == [QUESTION, "dog Rex"]

    async def test_the_second_grade_sees_what_both_searches_found(self):
        provider = FakeLLMProvider(grades=[insufficient("dog Rex"), EvidenceGrade(sufficient=True)])
        retriever = ScriptedRetriever(
            {QUESTION: [retrieved(HOMEWORK)], "dog Rex": [retrieved(DOG)]}
        )

        await run_turn(provider, retriever)

        second = provider.prompts_named("retrieval.grade")[1].user
        assert HOMEWORK in second
        assert DOG in second

    async def test_the_grader_is_told_which_searches_were_already_tried(self):
        provider = FakeLLMProvider(grades=[insufficient("dog Rex"), EvidenceGrade(sufficient=True)])
        retriever = ScriptedRetriever({QUESTION: [retrieved(HOMEWORK)]})

        await run_turn(provider, retriever)

        second = provider.prompts_named("retrieval.grade")[1].user
        tried = second[second.index("Searches already tried:") :]
        assert QUESTION in tried
        assert "dog Rex" in tried

    async def test_a_failed_embedding_still_searches_again_by_keyword(self):
        embeddings = FakeEmbeddingProvider(failure=LLMUnavailableError("embeddings down"))
        provider = FakeLLMProvider(grades=[insufficient("dog Rex"), EvidenceGrade(sufficient=True)])
        retriever = ScriptedRetriever(
            {QUESTION: [retrieved(HOMEWORK)], "dog Rex": [retrieved(DOG)]}
        )

        result = await run_turn(provider, retriever, embeddings=embeddings)

        assert retriever.queries == [QUESTION, "dog Rex"]
        assert retriever.embeddings == [None, None]
        assert DOG in reply_prompt(provider)
        assert result["errors"].count("query_embedding_failed") == 2


class TestTermination:
    @pytest.mark.parametrize("attempts", [1, 2, 3])
    async def test_a_grader_that_is_never_satisfied_stops_at_the_budget(self, attempts: int):
        """The bound holds even when every grade asks for a new search."""
        provider = FakeLLMProvider(
            grades=[insufficient(f"search number {index}") for index in range(10)]
        )
        retriever = ScriptedRetriever({QUESTION: [retrieved(HOMEWORK)]})

        result = await run_turn(
            provider, retriever, EvidencePolicy(max_retrieval_attempts=attempts)
        )

        assert len(retriever.queries) == attempts
        assert result["retrieval_attempts"] == attempts
        # Graded once per retrieval: the last grade decides what the reply is told.
        assert len(provider.prompts_named("retrieval.grade")) == attempts
        assert len(provider.prompts_named("conversation.reply")) == 1

    async def test_evidence_still_insufficient_at_the_end_is_admitted(self):
        provider = FakeLLMProvider(grades=[insufficient("dog Rex"), insufficient("pet")])
        retriever = ScriptedRetriever({QUESTION: [retrieved(HOMEWORK)]})

        result = await run_turn(provider, retriever)

        assert result["retrieval_attempts"] == 2
        assert "did not find everything" in reply_prompt(provider)

    async def test_repeating_a_search_already_tried_ends_the_loop(self):
        provider = FakeLLMProvider(grades=[insufficient(QUESTION.upper())])
        retriever = ScriptedRetriever({QUESTION: [retrieved(HOMEWORK)]})

        result = await run_turn(provider, retriever, EvidencePolicy(max_retrieval_attempts=3))

        assert retriever.queries == [QUESTION]
        assert result["retrieval_attempts"] == 1

    async def test_an_insufficient_grade_with_no_search_ends_the_loop(self):
        provider = FakeLLMProvider(grades=[insufficient("")])
        retriever = ScriptedRetriever({QUESTION: [retrieved(HOMEWORK)]})

        await run_turn(provider, retriever)

        assert retriever.queries == [QUESTION]
        assert "did not find everything" in reply_prompt(provider)

    async def test_a_retry_reached_without_a_search_still_counts_as_an_attempt(self):
        """Termination must not depend on routing having been right."""
        node = build_retry_retrieval_node(
            FakeEmbeddingProvider(), ScriptedRetriever({}), EvidencePolicy()
        )

        update = await node(
            {
                "user_id": USER_ID,
                "message": QUESTION,
                "searches": [QUESTION],
                "retrieval_attempts": 1,
                "evidence_grade": EvidenceGrade(sufficient=True),
            }
        )

        assert update == {"retrieval_attempts": 2}


class TestWhenGradingIsSkipped:
    async def test_an_empty_recall_is_not_graded(self):
        provider = FakeLLMProvider()
        retriever = ScriptedRetriever({})

        await run_turn(provider, retriever)

        assert provider.prompts_named("retrieval.grade") == []
        assert retriever.queries == [QUESTION]

    async def test_a_question_needing_no_recall_is_not_graded(self):
        provider = FakeLLMProvider(plans=[QueryPlan(needs=[RetrievalNeed.TOTALS])])
        retriever = ScriptedRetriever({QUESTION: [retrieved(DOG)]})

        await run_turn(provider, retriever)

        assert retriever.queries == []
        assert provider.prompts_named("retrieval.grade") == []

    async def test_a_disabled_policy_uses_memories_as_retrieved(self):
        provider = FakeLLMProvider()
        retriever = ScriptedRetriever({QUESTION: [retrieved(HOMEWORK), retrieved(DOG)]})

        await run_turn(provider, retriever, EvidencePolicy(enabled=False))

        assert provider.prompts_named("retrieval.grade") == []
        assert HOMEWORK in reply_prompt(provider)
        assert DOG in reply_prompt(provider)


class TestGradingFailure:
    async def test_a_failed_grade_uses_memories_as_retrieved(self):
        provider = GradingFailsProvider()
        retriever = ScriptedRetriever({QUESTION: [retrieved(HOMEWORK), retrieved(DOG)]})

        result = await run_turn(provider, retriever)

        assert retriever.queries == [QUESTION]
        assert HOMEWORK in reply_prompt(provider)
        assert DOG in reply_prompt(provider)
        assert "evidence_grading_failed" in result["errors"]
        assert result["degraded"] is False

    async def test_a_failed_grade_does_not_claim_the_evidence_was_incomplete(self):
        """Nothing was judged, so there is no finding to report to the reply."""
        provider = GradingFailsProvider()
        retriever = ScriptedRetriever({QUESTION: [retrieved(HOMEWORK)]})

        await run_turn(provider, retriever)

        assert "did not find everything" not in reply_prompt(provider)
