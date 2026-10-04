import uuid
from typing import Any

import pytest

from app.domain.conversation.context import DEFAULT_CONTEXT_BUDGET
from app.domain.conversation.verification import (
    AnswerVerification,
    UnsupportedReason,
    UnsupportedStatement,
)
from app.graph.conversation.graph import build_conversation_graph
from app.infrastructure.llm.tracing import TracingEmbeddingProvider, TracingLLMProvider
from app.infrastructure.observability.tracing import RunTrace, SpanStatus, bind_trace
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider, NoMemoryRetriever

INVENTED = "Since you ran a marathon last spring, add one long run a week."
HONEST = "I do not know your recent training, so start with three easy runs."


async def traced_turn(provider: FakeLLMProvider, retriever: Any = None) -> RunTrace:
    graph = build_conversation_graph(
        TracingLLMProvider(provider),
        DEFAULT_CONTEXT_BUDGET,
        TracingEmbeddingProvider(FakeEmbeddingProvider()),
        retriever or NoMemoryRetriever(),
    )
    with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
        await graph.ainvoke({"user_id": str(uuid.uuid4()), "message": "q", "errors": []})
    return trace


def calls_by_node(trace: RunTrace) -> list[tuple[str, list[str]]]:
    return [(step.node, [call.name for call in step.model_calls]) for step in trace.steps]


class TestConversationTrace:
    async def test_every_step_is_recorded_in_order_with_its_model_calls(self):
        trace = await traced_turn(FakeLLMProvider(replies=[HONEST]))

        assert calls_by_node(trace) == [
            ("understand_query", ["retrieval.plan"]),
            ("retrieve_context", ["embedding.query"]),
            ("build_context", []),
            ("generate_reply", ["conversation.reply"]),
            ("verify_answer", ["conversation.verify"]),
        ]
        assert trace.other_calls == []

    async def test_a_regeneration_appears_as_its_own_steps(self):
        flag = AnswerVerification(
            unsupported=[
                UnsupportedStatement(
                    quote="you ran a marathon last spring", reason=UnsupportedReason.UNSUPPORTED
                )
            ]
        )
        provider = FakeLLMProvider(
            replies=[INVENTED, HONEST], verifications=[flag, AnswerVerification()]
        )

        trace = await traced_turn(provider)

        assert [step.node for step in trace.steps][-4:] == [
            "generate_reply",
            "verify_answer",
            "generate_reply",
            "verify_answer",
        ]

    async def test_steps_are_laid_out_in_time(self):
        trace = await traced_turn(FakeLLMProvider())

        offsets = [step.offset_ms for step in trace.steps]
        assert offsets == sorted(offsets)

    async def test_a_step_that_raises_is_marked_as_the_failure(self):
        class BrokenRetriever:
            async def retrieve(self, *args: Any, **kwargs: Any) -> list[Any]:
                raise ConnectionError("database went away")

        graph = build_conversation_graph(
            TracingLLMProvider(FakeLLMProvider()),
            DEFAULT_CONTEXT_BUDGET,
            FakeEmbeddingProvider(),
            BrokenRetriever(),
        )
        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            with pytest.raises(ConnectionError):
                await graph.ainvoke({"user_id": str(uuid.uuid4()), "message": "q", "errors": []})

        failed = trace.failed_step()
        assert failed is not None
        assert failed.node == "retrieve_context"
        assert failed.status is SpanStatus.FAILED
        assert failed.error_type == "ConnectionError"
