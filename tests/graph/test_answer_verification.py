import uuid
from typing import Any

import pytest
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from app.domain.conversation.context import DEFAULT_CONTEXT_BUDGET
from app.domain.conversation.verification import (
    AnswerVerification,
    UnsupportedReason,
    UnsupportedStatement,
    VerificationPolicy,
)
from app.domain.memory.types import MemoryType
from app.domain.retrieval.grading import EvidenceGrade, EvidencePolicy
from app.graph.conversation.graph import build_conversation_graph, max_steps
from app.graph.conversation.nodes.generate_reply import FALLBACK_REPLY
from app.infrastructure.llm.exceptions import LLMInvalidOutputError, LLMUnavailableError
from app.infrastructure.llm.prompt import Prompt
from app.retrieval.types import RetrievalSource, RetrievedMemory
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider, NoMemoryRetriever

QUESTION = "How should I train this week?"

INVENTED = "Since you ran a marathon last spring, add a long run."
HONEST = "I do not know your recent training, so start with three easy runs."
WORSE = "Since you ran a marathon last spring and love hills, add hill repeats."


def flags(*quotes: str) -> AnswerVerification:
    return AnswerVerification(
        unsupported=[
            UnsupportedStatement(quote=quote, reason=UnsupportedReason.UNSUPPORTED)
            for quote in quotes
        ]
    )


CLEAN = AnswerVerification()
FLAG_MARATHON = flags("you ran a marathon last spring")


class FailingOnSchema(FakeLLMProvider):
    """Fails structured calls for one schema, so the rest of the turn runs."""

    def __init__(self, schema: type[BaseModel], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.failing = schema

    async def generate_structured[ModelT: BaseModel](
        self, prompt: Prompt, schema: type[ModelT]
    ) -> ModelT:
        if schema is self.failing:
            self.prompts.append(prompt)
            raise LLMInvalidOutputError("unusable")
        return await super().generate_structured(prompt, schema)


class FailingAfterFirstReply(FakeLLMProvider):
    """Writes one draft, then the provider goes down for replies."""

    async def generate_text(self, prompt: Prompt) -> str:
        if self.prompts_named("conversation.reply"):
            self.prompts.append(prompt)
            raise LLMUnavailableError("provider is down")
        return await super().generate_text(prompt)


async def run_turn(
    provider: FakeLLMProvider, policy: VerificationPolicy | None = None
) -> dict[str, Any]:
    graph = build_conversation_graph(
        provider,
        DEFAULT_CONTEXT_BUDGET,
        FakeEmbeddingProvider(),
        NoMemoryRetriever(),
        verification_policy=policy or VerificationPolicy(),
    )
    result: dict[str, Any] = await graph.ainvoke(
        {"user_id": str(uuid.uuid4()), "message": QUESTION, "errors": []}
    )
    return result


class TestGroundedReplies:
    async def test_a_supported_reply_is_returned_after_one_check(self):
        provider = FakeLLMProvider(replies=[HONEST], verifications=[CLEAN])

        result = await run_turn(provider)

        assert result["reply"] == HONEST
        assert result["grounded"] is True
        assert result["generation_attempts"] == 1
        assert len(provider.prompts_named("conversation.verify")) == 1

    async def test_the_verifier_checks_the_draft_it_was_given(self):
        provider = FakeLLMProvider(replies=[HONEST])

        await run_turn(provider)

        assert HONEST in provider.prompts_named("conversation.verify")[0].user


class TestRegeneration:
    async def test_an_unsupported_reply_is_regenerated_without_the_statement(self):
        provider = FakeLLMProvider(replies=[INVENTED, HONEST], verifications=[FLAG_MARATHON, CLEAN])

        result = await run_turn(provider)

        assert result["reply"] == HONEST
        assert result["grounded"] is True
        assert result["generation_attempts"] == 2

    async def test_the_retry_is_told_which_statements_to_avoid(self):
        provider = FakeLLMProvider(replies=[INVENTED, HONEST], verifications=[FLAG_MARATHON, CLEAN])

        await run_turn(provider)

        first, second = provider.prompts_named("conversation.reply")
        assert "A previous draft" not in first.user
        assert "you ran a marathon last spring" in second.user[second.user.index("A previous") :]

    async def test_a_finding_the_reply_does_not_contain_triggers_no_retry(self):
        """The verifier's word alone is not enough to reject a reply."""
        provider = FakeLLMProvider(replies=[HONEST], verifications=[flags("you love hills")])

        result = await run_turn(provider)

        assert result["grounded"] is True
        assert len(provider.prompts_named("conversation.reply")) == 1


class TestTermination:
    @pytest.mark.parametrize("attempts", [1, 2, 3])
    async def test_a_reply_that_never_passes_stops_at_the_budget(self, attempts: int):
        provider = FakeLLMProvider(replies=[INVENTED], verifications=[FLAG_MARATHON])

        result = await run_turn(provider, VerificationPolicy(max_generation_attempts=attempts))

        assert result["generation_attempts"] == attempts
        assert len(provider.prompts_named("conversation.reply")) == attempts
        assert len(provider.prompts_named("conversation.verify")) == attempts
        assert result["grounded"] is False
        assert result["reply"] == INVENTED

    async def test_a_worse_retry_does_not_replace_a_better_draft(self):
        provider = FakeLLMProvider(
            replies=[INVENTED, WORSE],
            verifications=[FLAG_MARATHON, flags("you ran a marathon last spring", "love hills")],
        )

        result = await run_turn(provider)

        assert result["reply"] == INVENTED
        assert result["grounded"] is False

    async def test_a_tie_keeps_the_earlier_draft(self):
        provider = FakeLLMProvider(
            replies=[INVENTED, WORSE], verifications=[FLAG_MARATHON, flags("love hills")]
        )

        result = await run_turn(provider)

        assert result["reply"] == INVENTED

    async def test_a_better_retry_that_still_fails_is_preferred(self):
        provider = FakeLLMProvider(
            replies=[WORSE, INVENTED],
            verifications=[flags("ran a marathon last spring", "love hills"), FLAG_MARATHON],
        )

        result = await run_turn(provider)

        assert result["reply"] == INVENTED
        assert result["grounded"] is False


class TestFailures:
    async def test_a_failed_verification_returns_the_reply_unverified(self):
        provider = FailingOnSchema(AnswerVerification, replies=[INVENTED])

        result = await run_turn(provider)

        assert result["reply"] == INVENTED
        assert result["grounded"] is None
        assert "answer_verification_failed" in result["errors"]
        assert len(provider.prompts_named("conversation.reply")) == 1

    async def test_a_failed_first_draft_is_the_fallback_and_is_not_verified(self):
        provider = FakeLLMProvider(failure=LLMUnavailableError("down"))

        result = await run_turn(provider)

        assert result["reply"] == FALLBACK_REPLY
        assert result["degraded"] is True
        assert result["grounded"] is None
        assert provider.prompts_named("conversation.verify") == []

    async def test_a_failed_regeneration_keeps_the_earlier_draft(self):
        """A flagged answer still answers; the fallback text does not."""
        provider = FailingAfterFirstReply(replies=[INVENTED], verifications=[FLAG_MARATHON])

        result = await run_turn(provider)

        assert result["reply"] == INVENTED
        assert result["degraded"] is False
        assert result["grounded"] is False
        assert result["generation_attempts"] == 2
        assert "reply_generation_failed" in result["errors"]


class TestPolicy:
    async def test_a_disabled_policy_returns_the_first_draft_unchecked(self):
        provider = FakeLLMProvider(replies=[INVENTED], verifications=[FLAG_MARATHON])

        result = await run_turn(provider, VerificationPolicy(enabled=False))

        assert result["reply"] == INVENTED
        assert result.get("grounded") is None
        assert provider.prompts_named("conversation.verify") == []


class AlwaysRecalls:
    async def retrieve(self, user_id: Any, query_text: str, *args: Any, **kwargs: Any):
        return [
            RetrievedMemory(
                id=uuid.uuid5(uuid.NAMESPACE_URL, query_text),
                content=f"A memory found by {query_text}",
                memory_type=MemoryType.FACT,
                importance=0.5,
                relevance=0.5,
                sources=(RetrievalSource.KEYWORD,),
            )
        ]


def exhausting_graph(retrieval: int, generation: int) -> Any:
    """A graph whose grader and verifier are never satisfied."""
    provider = FakeLLMProvider(
        replies=[INVENTED],
        verifications=[FLAG_MARATHON],
        grades=[
            EvidenceGrade(sufficient=False, search_query=f"search {index}")
            for index in range(retrieval + 1)
        ],
    )
    return build_conversation_graph(
        provider,
        DEFAULT_CONTEXT_BUDGET,
        FakeEmbeddingProvider(),
        AlwaysRecalls(),
        evidence_policy=EvidencePolicy(max_retrieval_attempts=retrieval),
        verification_policy=VerificationPolicy(max_generation_attempts=generation),
    )


class TestBothLoopsTogether:
    @pytest.mark.parametrize("retrieval, generation", [(1, 1), (2, 2), (3, 5), (5, 3)])
    async def test_both_budgets_spent_in_full_fit_the_step_limit(
        self, retrieval: int, generation: int
    ):
        graph = exhausting_graph(retrieval, generation)

        result = await graph.ainvoke(
            {"user_id": str(uuid.uuid4()), "message": QUESTION, "errors": []}
        )

        assert result["retrieval_attempts"] == retrieval
        assert result["generation_attempts"] == generation
        assert result["reply"] == INVENTED

    @pytest.mark.parametrize("retrieval, generation", [(1, 1), (2, 2), (3, 5)])
    async def test_the_step_limit_has_no_slack(self, retrieval: int, generation: int):
        """One step fewer and the worst case fails, so an unplanned step would too."""
        graph = exhausting_graph(retrieval, generation)
        assert graph.config is not None
        tighter = graph.with_config(recursion_limit=graph.config["recursion_limit"] - 1)

        with pytest.raises(GraphRecursionError):
            await tighter.ainvoke({"user_id": str(uuid.uuid4()), "message": QUESTION, "errors": []})

    def test_the_default_policies_allow_ten_steps(self):
        assert max_steps(EvidencePolicy(), VerificationPolicy()) == 10
