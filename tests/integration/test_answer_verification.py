"""Answer verification end to end: real HTTP, real database, a scripted verifier.

The verifier is scripted because what is under test is what the system does
with its findings; whether a real verifier finds the right ones is evaluation's
question.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.conversation.context import MessageRole
from app.domain.conversation.verification import (
    AnswerVerification,
    UnsupportedReason,
    UnsupportedStatement,
)
from app.infrastructure.database.models import Message
from tests.fakes import FakeLLMProvider
from tests.integration.test_semantic_retrieval import authenticate

pytestmark = pytest.mark.integration

INVENTED = "Since you ran a marathon last spring, add one long run a week."
HONEST = "I do not know your recent training, so start with three easy runs."

FLAG = AnswerVerification(
    unsupported=[
        UnsupportedStatement(
            quote="you ran a marathon last spring", reason=UnsupportedReason.UNSUPPORTED
        )
    ]
)


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider()


async def send(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/chat", headers=headers, json={"message": "How should I train?"}
    )
    assert response.status_code == 201
    data: dict[str, Any] = response.json()["data"]
    return data


def extracted_replies(provider: FakeLLMProvider) -> str:
    prompts = provider.prompts_named("memory.extraction")
    assert prompts, "memory extraction did not run"
    user = prompts[-1].user
    return user[user.index("The assistant replied:") :]


class TestResponse:
    async def test_a_verified_reply_is_reported_grounded(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_replies(HONEST)

        data = await send(api_client, headers)

        assert data["grounded"] is True
        assert data["reply"]["content"] == HONEST

    async def test_a_corrected_reply_replaces_the_unsupported_draft(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_replies(INVENTED, HONEST)
        llm_provider.queue_verifications(FLAG, AnswerVerification())

        data = await send(api_client, headers)

        assert data["grounded"] is True
        assert data["reply"]["content"] == HONEST

    async def test_a_reply_that_never_passes_is_reported_ungrounded(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_replies(INVENTED)
        llm_provider.queue_verifications(FLAG)

        data = await send(api_client, headers)

        assert data["grounded"] is False
        assert data["degraded"] is False
        assert data["reply"]["content"] == INVENTED


class TestPersistence:
    async def test_only_the_returned_draft_is_stored(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_replies(INVENTED, HONEST)
        llm_provider.queue_verifications(FLAG, AnswerVerification())

        await send(api_client, headers)

        stored = (
            await db_session.scalars(
                select(Message.content).where(Message.role == MessageRole.ASSISTANT)
            )
        ).all()
        assert stored == [HONEST]


class TestMemoryExtraction:
    async def test_a_grounded_reply_is_given_to_extraction(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_replies(HONEST)

        await send(api_client, headers)

        assert HONEST in extracted_replies(llm_provider)

    async def test_an_ungrounded_reply_is_kept_away_from_extraction(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """Remembering an invented claim would make it evidence for later answers."""
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_replies(INVENTED)
        llm_provider.queue_verifications(FLAG)

        await send(api_client, headers)

        assert "marathon" not in extracted_replies(llm_provider)
