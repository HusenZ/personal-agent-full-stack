import asyncio
import uuid
from typing import Any

import pytest

from app.domain.memory.types import MemoryType
from app.domain.retrieval.plan import QueryPlan, RetrievalNeed
from app.domain.temporal.ranges import TimeRangeSpec
from app.graph.conversation.nodes.retrieve_context import build_retrieve_context_node
from app.infrastructure.llm.exceptions import LLMUnavailableError
from app.retrieval.structured import ActivitySummary
from app.retrieval.types import RetrievalSource, RetrievedMemory

USER_ID = str(uuid.uuid4())
ALL_NEEDS = QueryPlan(
    needs=[RetrievalNeed.SEMANTIC, RetrievalNeed.TIMELINE, RetrievalNeed.TOTALS],
    time_range=TimeRangeSpec.LAST_7_DAYS,
)


class Recorder:
    """Records the order in which work starts and finishes across fakes."""

    def __init__(self) -> None:
        self.events: list[str] = []


class SlowEmbeddings:
    def __init__(self, recorder: Recorder, *, fail: bool = False, delay: float = 0.05) -> None:
        self.recorder = recorder
        self.fail = fail
        self.delay = delay
        self.cancelled = False

    async def embed_query(self, text: str) -> list[float]:
        self.recorder.events.append("embed_started")
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.recorder.events.append("embed_finished")
        if self.fail:
            raise LLMUnavailableError("embeddings down")
        return [0.1, 0.2]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed_query(text) for text in texts]


class RecordingTimeline:
    def __init__(self, recorder: Recorder, *, fail: bool = False) -> None:
        self.recorder = recorder
        self.fail = fail

    async def timeline(self, *args: Any, **kwargs: Any) -> list[Any]:
        self.recorder.events.append("timeline_ran")
        if self.fail:
            raise RuntimeError("database went away")
        return []


class RecordingTotals:
    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    async def summarise(self, user_id: Any, time_range: Any, **kwargs: Any) -> ActivitySummary:
        self.recorder.events.append("totals_ran")
        return ActivitySummary(time_range=time_range, totals=())


class RecordingRetriever:
    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder
        self.embedding_seen: list[float] | str | None = "not called"

    async def retrieve(
        self, user_id: Any, query_text: str, query_embedding: list[float] | None, **kwargs: Any
    ) -> list[RetrievedMemory]:
        self.recorder.events.append("memories_retrieved")
        self.embedding_seen = query_embedding
        return [
            RetrievedMemory(
                id=uuid.uuid4(),
                content="I play the cello",
                memory_type=MemoryType.FACT,
                importance=0.5,
                relevance=0.8,
                sources=(RetrievalSource.KEYWORD,),
            )
        ]


def node(recorder: Recorder, **overrides: Any):
    parts: dict[str, Any] = {
        "embeddings": SlowEmbeddings(recorder),
        "retriever": RecordingRetriever(recorder),
        "timeline": RecordingTimeline(recorder),
        "totals": RecordingTotals(recorder),
    }
    parts.update(overrides)
    return build_retrieve_context_node(
        parts["embeddings"],
        parts["retriever"],
        parts["timeline"],
        parts["totals"],
        timeline_limit=25,
    ), parts


def state(plan: QueryPlan = ALL_NEEDS) -> Any:
    return {"user_id": USER_ID, "message": "cello practice this week", "plan": plan, "errors": []}


class TestOverlap:
    async def test_the_embedding_call_overlaps_the_database_work(self):
        """The one place concurrency pays: a different service from the database.
        The database queries run while the embedding is still in flight."""
        recorder = Recorder()
        retrieve, _ = node(recorder)

        await retrieve(state())

        events = recorder.events
        assert events.index("embed_started") < events.index("timeline_ran")
        assert events.index("timeline_ran") < events.index("embed_finished")
        assert events.index("totals_ran") < events.index("embed_finished")

    async def test_database_queries_run_one_after_another(self):
        """They share one session; order is sequential, never interleaved."""
        recorder = Recorder()
        retrieve, _ = node(recorder)

        await retrieve(state())

        db_events = [e for e in recorder.events if e.endswith("_ran") or e == "memories_retrieved"]
        assert db_events == ["timeline_ran", "totals_ran", "memories_retrieved"]

    async def test_memories_are_retrieved_with_the_computed_embedding(self):
        recorder = Recorder()
        retrieve, parts = node(recorder)

        await retrieve(state())

        assert parts["retriever"].embedding_seen == [0.1, 0.2]


class TestDegradation:
    async def test_a_failed_embedding_still_retrieves_by_keyword(self):
        """The retriever is called with no embedding rather than not at all."""
        recorder = Recorder()
        retrieve, parts = node(recorder, embeddings=SlowEmbeddings(recorder, fail=True))

        result = await retrieve(state())

        assert parts["retriever"].embedding_seen is None
        assert [memory.content for memory in result["memories"]] == ["I play the cello"]
        assert "query_embedding_failed" in result["errors"]

    async def test_a_database_failure_cancels_the_embedding_call(self):
        """Otherwise the call keeps running with nobody waiting for its result."""
        recorder = Recorder()
        embeddings = SlowEmbeddings(recorder, delay=5)
        retrieve, _ = node(
            recorder, embeddings=embeddings, timeline=RecordingTimeline(recorder, fail=True)
        )

        with pytest.raises(RuntimeError, match="database went away"):
            await retrieve(state())

        await asyncio.sleep(0)
        assert embeddings.cancelled


class TestPlanIsRespected:
    async def test_nothing_is_embedded_when_recall_is_not_needed(self):
        recorder = Recorder()
        retrieve, _ = node(recorder)

        await retrieve(state(QueryPlan(needs=[RetrievalNeed.TOTALS])))

        assert "embed_started" not in recorder.events
        assert "memories_retrieved" not in recorder.events

    async def test_unrequested_sources_are_not_queried(self):
        recorder = Recorder()
        retrieve, _ = node(recorder)

        await retrieve(state(QueryPlan(needs=[RetrievalNeed.SEMANTIC])))

        assert "timeline_ran" not in recorder.events
        assert "totals_ran" not in recorder.events
