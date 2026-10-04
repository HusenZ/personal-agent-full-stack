"""The retrieval loop end to end: real HTTP, real database, a scripted grader.

The grader is scripted because what is under test is what the system does with
a grade, not whether a model grades well; that is evaluation's question.
"""

import pytest
from httpx import AsyncClient

from app.domain.retrieval.grading import EvidenceGrade
from tests.fakes import FakeLLMProvider
from tests.integration.test_semantic_retrieval import (
    authenticate,
    candidate,
    chat,
    recalled_section,
    remember,
)

pytestmark = pytest.mark.integration

# Shares a word ("getting") with the question but not its subject.
DISTRACTOR = "I am getting on well with my new manager"
# The answer, with no word in common with the question.
ANSWER = "My greyhound Biscuit loves long walks"

QUESTION = "How is my pet getting on?"


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(replies=["Understood."])


def last_reply_prompt(provider: FakeLLMProvider) -> str:
    return provider.prompts_named("conversation.reply")[-1].user


class TestRecoveringFromBadRecall:
    async def test_the_first_search_finds_only_the_distractor(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """Establishes the premise: without a rewrite, the answer is missed."""
        headers = await authenticate(api_client, "ada@example.com")
        await remember(api_client, llm_provider, headers, candidate(DISTRACTOR), candidate(ANSWER))

        await chat(api_client, headers, QUESTION)

        grade_prompt = llm_provider.prompts_named("retrieval.grade")[-1].user
        assert DISTRACTOR in grade_prompt
        assert ANSWER not in grade_prompt

    async def test_a_rewritten_search_recovers_the_answer(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await remember(api_client, llm_provider, headers, candidate(DISTRACTOR), candidate(ANSWER))

        llm_provider.queue_grades(
            EvidenceGrade(irrelevant=[1], sufficient=False, search_query="greyhound dog"),
            EvidenceGrade(irrelevant=[2], sufficient=True),
        )
        await chat(api_client, headers, QUESTION)

        recalled = recalled_section(last_reply_prompt(llm_provider))
        assert ANSWER in recalled
        assert DISTRACTOR not in recalled
        assert "did not find everything" not in last_reply_prompt(llm_provider)

    async def test_a_rewritten_search_never_reaches_another_users_memories(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """The rewrite is model-written, so the scoping must not depend on it."""
        other = await authenticate(api_client, "grace@example.com")
        await remember(
            api_client, llm_provider, other, candidate("My greyhound Shadow is a rescue dog")
        )

        headers = await authenticate(api_client, "ada@example.com")
        await remember(api_client, llm_provider, headers, candidate(DISTRACTOR))

        llm_provider.queue_grades(
            EvidenceGrade(irrelevant=[1], sufficient=False, search_query="greyhound dog"),
            EvidenceGrade(sufficient=False),
        )
        await chat(api_client, headers, QUESTION)

        assert "Shadow" not in last_reply_prompt(llm_provider)
        assert "Shadow" not in llm_provider.prompts_named("retrieval.grade")[-1].user
        assert "did not find everything" in last_reply_prompt(llm_provider)

    async def test_the_exchange_is_stored_as_usual_after_a_retry(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await remember(api_client, llm_provider, headers, candidate(DISTRACTOR), candidate(ANSWER))

        llm_provider.queue_grades(
            EvidenceGrade(sufficient=False, search_query="greyhound dog"),
            EvidenceGrade(sufficient=True),
        )
        response = await api_client.post(
            "/api/v1/chat", headers=headers, json={"message": QUESTION}
        )

        assert response.status_code == 201
        body = response.json()["data"]
        assert body["reply"]["content"] == "Understood."
        assert body["degraded"] is False
