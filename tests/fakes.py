"""Test doubles for the model provider.

Every automated test runs against these rather than a live model: a suite that
depends on a provider is slow, costs money, and fails for reasons unrelated to
the code under test.
"""

import hashlib
from collections.abc import Sequence
from math import sqrt
from typing import Any, cast

from pydantic import BaseModel

from app.domain.analysis.claims import AnalysisResult
from app.domain.conversation.verification import AnswerVerification
from app.domain.memory.decisions import MemoryJudgment
from app.domain.memory.similarity import tokenise
from app.domain.memory.types import MemoryExtraction
from app.domain.onboarding.context import ExtractionResult
from app.domain.planning.week import ProposedWeek
from app.domain.retrieval.grading import EvidenceGrade
from app.domain.retrieval.plan import QueryPlan
from app.graph.onboarding.nodes.generate_question import GeneratedQuestion
from app.infrastructure.llm.embeddings import EMBEDDING_DIMENSIONS
from app.infrastructure.llm.exceptions import LLMError, LLMUnavailableError
from app.infrastructure.llm.prompt import Prompt


def _next_from(queue: list[Any], empty: Any) -> Any:
    """Take the next queued response, repeating the last one once exhausted.

    Repeating rather than raising means a test that sends several messages does
    not have to enumerate a response for each, and a retry loop does not fall off
    the end of the queue and quietly change what it is testing.
    """
    if len(queue) > 1:
        return queue.pop(0)
    if queue:
        return queue[0]
    return empty()


class FakeLLMProvider:
    """Returns queued responses and records the prompts it was given."""

    def __init__(
        self,
        extractions: Sequence[ExtractionResult] | None = None,
        questions: Sequence[str] | None = None,
        analyses: Sequence[AnalysisResult] | None = None,
        replies: Sequence[str] | None = None,
        plans: Sequence[QueryPlan] | None = None,
        extractions_of_memory: Sequence[MemoryExtraction] | None = None,
        judgments: Sequence[MemoryJudgment] | None = None,
        grades: Sequence[EvidenceGrade] | None = None,
        verifications: Sequence[AnswerVerification] | None = None,
        judgements: Sequence[BaseModel] | None = None,
        weeks: Sequence[ProposedWeek] | None = None,
        failure: LLMError | None = None,
    ) -> None:
        self._extractions = list(extractions or [])
        self._questions = list(questions or [])
        self._analyses = list(analyses or [])
        self._replies = list(replies or [])
        self._plans = list(plans or [])
        self._memory_extractions = list(extractions_of_memory or [])
        self._judgments = list(judgments or [])
        self._grades = list(grades or [])
        self._verifications = list(verifications or [])
        self._judgements = list(judgements or [])
        self._weeks = list(weeks or [])
        self._failure = failure
        self.prompts: list[Prompt] = []

    async def generate_structured[ModelT: BaseModel](
        self, prompt: Prompt, schema: type[ModelT]
    ) -> ModelT:
        self.prompts.append(prompt)

        if self._failure is not None:
            raise self._failure

        queues: dict[type[BaseModel], tuple[list[Any], Any]] = {
            ExtractionResult: (self._extractions, ExtractionResult),
            AnalysisResult: (self._analyses, AnalysisResult),
            MemoryExtraction: (self._memory_extractions, MemoryExtraction),
            MemoryJudgment: (self._judgments, MemoryJudgment),
            QueryPlan: (self._plans, QueryPlan),
            # Sufficient by default, so non-grading tests see raw retrieval.
            EvidenceGrade: (self._grades, lambda: EvidenceGrade(sufficient=True)),
            # No findings by default, so non-verification tests keep the first draft.
            AnswerVerification: (self._verifications, AnswerVerification),
            ProposedWeek: (self._weeks, lambda: ProposedWeek(summary="")),
        }

        if schema in queues:
            queue, empty = queues[schema]
            return cast(ModelT, _next_from(queue, empty))

        if schema.__name__ == "AnswerJudgement":
            # Matched by name to avoid importing the evaluation package.
            if not self._judgements:
                raise AssertionError("FakeLLMProvider has no judgement queued")
            return cast(ModelT, _next_from(self._judgements, lambda: None))

        if schema is GeneratedQuestion:
            text = self._questions.pop(0) if self._questions else "What else matters to you?"
            return cast(ModelT, GeneratedQuestion(question=text))

        raise AssertionError(f"FakeLLMProvider has no response for {schema.__name__}")

    async def generate_text(self, prompt: Prompt) -> str:
        self.prompts.append(prompt)

        if self._failure is not None:
            raise self._failure

        if len(self._replies) > 1:
            return self._replies.pop(0)
        return self._replies[0] if self._replies else "A generated reply."

    def queue_memory_extractions(self, *extractions: MemoryExtraction) -> None:
        self._memory_extractions = list(extractions)

    def queue_judgments(self, *judgments: MemoryJudgment) -> None:
        self._judgments = list(judgments)

    def queue_verifications(self, *verifications: AnswerVerification) -> None:
        self._verifications = list(verifications)

    def queue_grades(self, *grades: EvidenceGrade) -> None:
        self._grades = list(grades)

    def queue_weeks(self, *weeks: ProposedWeek) -> None:
        self._weeks.extend(weeks)

    def queue_plans(self, *plans: QueryPlan) -> None:
        self._plans = list(plans)

    def queue_replies(self, *replies: str) -> None:
        self._replies = list(replies)

    def queue_extractions(self, *extractions: ExtractionResult) -> None:
        self._extractions = list(extractions)

    def queue_analyses(self, *analyses: AnalysisResult) -> None:
        self._analyses = list(analyses)

    def prompts_named(self, name: str) -> list[Prompt]:
        return [prompt for prompt in self.prompts if prompt.name == name]


class UnavailableLLMProvider(FakeLLMProvider):
    """Fails every call, for exercising degraded paths."""

    def __init__(self) -> None:
        super().__init__(failure=LLMUnavailableError("provider is down"))


class FakeEmbeddingProvider:
    """Deterministic embeddings, derived from the words in the text.

    Each token is hashed into one dimension, so texts sharing vocabulary end up
    with similar vectors and unrelated texts end up near-orthogonal. That makes
    retrieval tests meaningful without a network call, while staying completely
    reproducible.
    """

    def __init__(self, failure: LLMError | None = None) -> None:
        self._failure = failure
        self.embedded: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self.embed_documents([text])
        return vectors[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._failure is not None:
            raise self._failure

        self.embedded.extend(texts)
        return [_project(text) for text in texts]


def _project(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS

    for token in tokenise(text):
        # md5, not hash(): Python salts str hashes per process.
        digest = hashlib.md5(token.encode(), usedforsecurity=False).hexdigest()

        # Several dimensions per token to avoid hash collisions looking related.
        for slice_start in range(0, 24, 8):
            bucket = int(digest[slice_start : slice_start + 8], 16) % EMBEDDING_DIMENSIONS
            vector[bucket] += 1.0

    norm = sqrt(sum(value * value for value in vector))
    if not norm:
        # Constant vector for empty input instead of raising.
        return [1.0 / sqrt(EMBEDDING_DIMENSIONS)] * EMBEDDING_DIMENSIONS

    return [value / norm for value in vector]


class NoMemoryRetriever:
    """Recalls nothing, for tests about the reply rather than about recall."""

    async def retrieve(
        self,
        user_id: Any,
        query_text: str,
        query_embedding: list[float] | None,
        *,
        now: Any = None,
    ) -> list[Any]:
        return []


def build_test_conversation_graph(
    provider: FakeLLMProvider,
    embeddings: "FakeEmbeddingProvider | None" = None,
    retriever: Any = None,
) -> Any:
    """A conversation workflow wired to fakes, for tests that script the model."""
    from app.domain.conversation.context import DEFAULT_CONTEXT_BUDGET
    from app.graph.conversation.graph import build_conversation_graph

    return build_conversation_graph(
        provider,
        DEFAULT_CONTEXT_BUDGET,
        embeddings or FakeEmbeddingProvider(),
        retriever or NoMemoryRetriever(),
    )
