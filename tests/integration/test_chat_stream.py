"""The streaming chat endpoint end to end: real HTTP, real database, fake model."""

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.conversation.verification import (
    AnswerVerification,
    UnsupportedReason,
    UnsupportedStatement,
)
from app.graph.conversation.nodes.generate_reply import FALLBACK_REPLY
from app.infrastructure.database.models import Conversation, Message
from app.infrastructure.llm.exceptions import LLMUnavailableError
from tests.fakes import FakeLLMProvider, build_test_conversation_graph
from tests.integration.test_chat import authenticate

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
    return FakeLLMProvider(replies=[HONEST])


def parse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Split a server-sent event stream into (event, data) pairs.

    Strict on purpose: every block must carry exactly one event line and one
    data line, so a malformed frame fails the test instead of being skipped.
    """
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        if not block:
            continue
        lines = block.split("\n")
        assert len(lines) == 2, f"malformed event block: {block!r}"
        name_line, data_line = lines
        assert name_line.startswith("event: ") and data_line.startswith("data: ")
        events.append((name_line.removeprefix("event: "), json.loads(data_line[6:])))
    return events


async def stream(
    client: AsyncClient,
    headers: dict[str, str],
    message: str = "How should I train?",
    conversation_id: uuid.UUID | None = None,
) -> tuple[int, dict[str, str], str]:
    payload: dict[str, Any] = {"message": message}
    if conversation_id is not None:
        payload["conversation_id"] = str(conversation_id)
    response = await client.post("/api/v1/chat/stream", headers=headers, json=payload)
    return response.status_code, dict(response.headers), response.text


def names(events: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [name for name, _ in events]


def last(events: list[tuple[str, dict[str, Any]]], name: str) -> dict[str, Any]:
    return [data for event, data in events if event == name][-1]


class TestTheStream:
    async def test_progress_comes_first_and_the_reply_last(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        status_code, response_headers, body = await stream(api_client, headers)

        assert status_code == 200
        assert response_headers["content-type"].startswith("text/event-stream")
        assert response_headers["cache-control"] == "no-cache, no-transform"
        assert names(parse(body)) == [
            "query_started",
            "query_understood",
            "retrieval_completed",
            "draft_completed",
            "verification_completed",
            "reply",
            "completed",
        ]

    async def test_the_reply_event_carries_the_text_and_its_verification(
        self, api_client: AsyncClient
    ):
        headers = await authenticate(api_client, "ada@example.com")

        events = parse((await stream(api_client, headers))[2])

        assert last(events, "reply") == {"content": HONEST, "grounded": True}

    async def test_completed_matches_the_non_streaming_response(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        streamed = last(parse((await stream(api_client, headers))[2]), "completed")
        direct = (
            await api_client.post(
                "/api/v1/chat", headers=headers, json={"message": "How should I train?"}
            )
        ).json()

        assert set(streamed["data"]) == set(direct["data"])
        assert streamed["data"]["reply"]["content"] == HONEST
        assert streamed["meta"]["request_id"]

    async def test_the_request_id_header_is_sent(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        _, response_headers, body = await stream(api_client, headers)

        completed = last(parse(body), "completed")
        assert response_headers["x-request-id"] == completed["meta"]["request_id"]


class TestOnlyVerifiedTextIsStreamed:
    async def test_a_rejected_draft_never_appears_in_the_stream(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_replies(INVENTED, HONEST)
        llm_provider.queue_verifications(FLAG, AnswerVerification())

        _, _, body = await stream(api_client, headers)

        assert "marathon" not in body
        events = parse(body)
        assert names(events).count("draft_completed") == 2
        assert last(events, "reply") == {"content": HONEST, "grounded": True}

    async def test_a_reply_that_never_passes_is_released_flagged(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """No draft passed, so the best one is sent marked as such, as POST does."""
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_replies(INVENTED)
        llm_provider.queue_verifications(FLAG)

        events = parse((await stream(api_client, headers))[2])

        assert last(events, "reply") == {"content": INVENTED, "grounded": False}
        assert last(events, "completed")["data"]["grounded"] is False


class TestPersistence:
    async def test_the_exchange_is_stored_before_the_reply_is_released(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")

        events = parse((await stream(api_client, headers))[2])

        stored = (await db_session.scalars(select(Message).order_by(Message.position))).all()
        assert [message.content for message in stored] == ["How should I train?", HONEST]
        assert last(events, "completed")["data"]["reply"]["id"] == str(stored[1].id)

    async def test_a_follow_up_continues_the_conversation(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        first = last(parse((await stream(api_client, headers))[2]), "completed")
        conversation_id = uuid.UUID(first["data"]["conversation_id"])

        second = last(
            parse((await stream(api_client, headers, "And next week?", conversation_id))[2]),
            "completed",
        )

        assert second["data"]["conversation_id"] == str(conversation_id)

    async def test_memory_extraction_runs_after_the_stream(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")

        await stream(api_client, headers)

        prompts = llm_provider.prompts_named("memory.extraction")
        assert len(prompts) == 1
        assert HONEST in prompts[0].user

    async def test_an_ungrounded_reply_is_kept_away_from_extraction(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_replies(INVENTED)
        llm_provider.queue_verifications(FLAG)

        await stream(api_client, headers)

        assert "marathon" not in llm_provider.prompts_named("memory.extraction")[0].user


class FailingGraph:
    """Fails part way through, as a lost database connection would."""

    async def astream(self, *args: Any, **kwargs: Any):
        yield ("updates", {"understand_query": {}})
        raise RuntimeError("connection to the database was lost at 10.0.0.5")


class TestFailures:
    async def test_a_failure_mid_stream_is_an_error_event_without_internals(
        self, api_client: AsyncClient, api_app: FastAPI
    ):
        headers = await authenticate(api_client, "ada@example.com")
        api_app.state.conversation_graph = FailingGraph()

        status_code, response_headers, body = await stream(api_client, headers)

        assert status_code == 200
        events = parse(body)
        assert names(events)[-1] == "error"
        assert "reply" not in names(events)
        error = last(events, "error")
        assert error["code"] == "TURN_FAILED"
        assert error["request_id"] == response_headers["x-request-id"]
        assert "10.0.0.5" not in body
        assert "RuntimeError" not in body

    async def test_a_failed_turn_stores_nothing(
        self, api_client: AsyncClient, api_app: FastAPI, db_session: AsyncSession
    ):
        """Not even the new conversation: the question was never answered."""
        headers = await authenticate(api_client, "ada@example.com")
        api_app.state.conversation_graph = FailingGraph()

        await stream(api_client, headers)

        assert await db_session.scalar(select(func.count()).select_from(Message)) == 0
        assert await db_session.scalar(select(func.count()).select_from(Conversation)) == 0

    async def test_a_model_outage_streams_the_fallback_as_degraded(
        self, api_client: AsyncClient, api_app: FastAPI
    ):
        headers = await authenticate(api_client, "ada@example.com")
        api_app.state.conversation_graph = build_test_conversation_graph(
            FakeLLMProvider(failure=LLMUnavailableError("down"))
        )

        events = parse((await stream(api_client, headers))[2])

        assert "verification_completed" not in names(events)
        assert last(events, "reply") == {"content": FALLBACK_REPLY, "grounded": None}
        assert last(events, "completed")["data"]["degraded"] is True


class TestBeforeTheStreamOpens:
    async def test_no_token_is_an_ordinary_401(self, api_client: AsyncClient):
        response = await api_client.post("/api/v1/chat/stream", json={"message": "hi"})

        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/json")

    async def test_an_invalid_message_is_an_ordinary_422(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.post(
            "/api/v1/chat/stream", headers=headers, json={"message": ""}
        )

        assert response.status_code == 422

    async def test_another_users_conversation_is_an_ordinary_404(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        owner = await authenticate(api_client, "grace@example.com")
        conversation_id = uuid.UUID(
            last(parse((await stream(api_client, owner))[2]), "completed")["data"][
                "conversation_id"
            ]
        )
        intruder = await authenticate(api_client, "ada@example.com")

        status_code, response_headers, body = await stream(
            api_client, intruder, conversation_id=conversation_id
        )

        assert status_code == 404
        assert response_headers["content-type"].startswith("application/json")
        assert json.loads(body)["error"]["code"] == "CONVERSATION_NOT_FOUND"
        assert await db_session.scalar(select(func.count()).select_from(Message)) == 2


class TestStorageComesFirst:
    async def test_a_reply_that_could_not_be_stored_is_never_released(
        self, plain_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        """Without a worker draining after it: every writing commit is made to fail."""
        api_client = plain_client
        headers = await authenticate(api_client, "ada@example.com")

        original_commit = AsyncSession.commit

        async def failing_commit(self: AsyncSession) -> None:
            # Only fail commits that write; the read-only prepare commit is fine.
            if self.new or self.dirty:
                raise RuntimeError("commit failed")
            await original_commit(self)

        # Patched after authenticating, so only the exchange's commit fails.
        monkeypatch.setattr(AsyncSession, "commit", failing_commit)

        _, _, body = await stream(api_client, headers)

        events = parse(body)
        assert names(events)[-1] == "error"
        assert "reply" not in names(events)
        assert HONEST not in body
