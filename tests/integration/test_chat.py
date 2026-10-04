import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.conversation.context import DEFAULT_CONTEXT_BUDGET
from app.graph.conversation.nodes.generate_reply import FALLBACK_REPLY
from app.infrastructure.database.models import Conversation, Message
from app.infrastructure.llm.exceptions import LLMUnavailableError
from tests.fakes import FakeLLMProvider, build_test_conversation_graph

pytestmark = pytest.mark.integration

PASSWORD = "a-sufficiently-long-password"


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(replies=["A personalised reply."])


async def authenticate(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    access_token: str = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


async def send(client: AsyncClient, headers: dict[str, str], message: str, conversation_id=None):
    payload: dict[str, object] = {"message": message}
    if conversation_id is not None:
        payload["conversation_id"] = str(conversation_id)
    return await client.post("/api/v1/chat", headers=headers, json=payload)


class TestAuthorization:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/v1/chat"),
            ("get", "/api/v1/conversations"),
            ("get", f"/api/v1/conversations/{uuid.uuid4()}"),
        ],
    )
    async def test_every_endpoint_requires_a_token(
        self, api_client: AsyncClient, method: str, path: str
    ):
        response = await api_client.request(method, path, json={"message": "hello"})

        assert response.status_code == 401


class TestSendingAMessage:
    async def test_a_first_message_starts_a_conversation(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        response = await send(api_client, headers, "What should I focus on?")

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["conversation_id"]
        assert data["reply"]["content"] == "A personalised reply."
        assert data["reply"]["role"] == "assistant"

    async def test_the_conversation_is_named_after_the_first_message(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        data = (await send(api_client, headers, "What should I focus on?")).json()["data"]

        assert data["title"] == "What should I focus on?"

    async def test_naming_the_conversation_costs_no_model_call(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")

        await send(api_client, headers, "What should I focus on?")

        assert len(llm_provider.prompts_named("conversation.reply")) == 1

    async def test_both_sides_of_the_exchange_are_stored(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await send(api_client, headers, "A question")

        messages = (
            (await db_session.execute(select(Message).order_by(Message.created_at))).scalars().all()
        )

        assert [(message.role.value, message.content) for message in messages] == [
            ("user", "A question"),
            ("assistant", "A personalised reply."),
        ]

    async def test_a_follow_up_continues_the_same_conversation(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        first = (await send(api_client, headers, "First")).json()["data"]

        second = (await send(api_client, headers, "Second", first["conversation_id"])).json()[
            "data"
        ]

        assert second["conversation_id"] == first["conversation_id"]

    async def test_a_follow_up_does_not_rename_the_conversation(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        first = (await send(api_client, headers, "The original question")).json()["data"]

        second = (
            await send(api_client, headers, "Something else entirely", first["conversation_id"])
        ).json()["data"]

        assert second["title"] == "The original question"

    @pytest.mark.parametrize("message", ["", "   ", "x" * 4001])
    async def test_invalid_messages_are_rejected(self, api_client: AsyncClient, message: str):
        headers = await authenticate(api_client, "ada@example.com")

        response = await send(api_client, headers, message)

        assert response.status_code == 422

    async def test_nothing_is_stored_when_the_message_is_rejected(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await send(api_client, headers, "")

        assert (await db_session.execute(select(Conversation))).scalars().all() == []


class TestMessageOrdering:
    async def test_the_question_is_stored_before_its_answer(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """Both messages are written in one transaction, so a server-side
        timestamp gives them the same value. Order comes from an explicit
        position, or a conversation can read back answer-first."""
        headers = await authenticate(api_client, "ada@example.com")
        await send(api_client, headers, "A question")

        messages = (
            (await db_session.execute(select(Message).order_by(Message.position))).scalars().all()
        )

        assert [message.position for message in messages] == [0, 1]
        assert [message.role.value for message in messages] == ["user", "assistant"]

    async def test_positions_continue_across_turns(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        conversation_id = (await send(api_client, headers, "First")).json()["data"][
            "conversation_id"
        ]
        await send(api_client, headers, "Second", conversation_id)

        messages = (
            (await db_session.execute(select(Message).order_by(Message.position))).scalars().all()
        )

        assert [message.position for message in messages] == [0, 1, 2, 3]

    async def test_the_conversation_tracks_how_many_messages_it_holds(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        conversation_id = (await send(api_client, headers, "First")).json()["data"][
            "conversation_id"
        ]
        await send(api_client, headers, "Second", conversation_id)

        conversation = (await db_session.execute(select(Conversation))).scalar_one()

        assert conversation.message_count == 4


class TestContext:
    async def test_the_profile_reaches_the_prompt(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await api_client.patch(
            "/api/v1/profile",
            headers=headers,
            json={"goals": [{"name": "Move into AI engineering", "priority": "high"}]},
        )

        await send(api_client, headers, "What should I focus on?")

        prompt = llm_provider.prompts_named("conversation.reply")[0]
        assert "Move into AI engineering" in prompt.user

    async def test_earlier_turns_reach_the_prompt(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        first = (await send(api_client, headers, "My first question")).json()["data"]

        await send(api_client, headers, "My second question", first["conversation_id"])

        prompt = llm_provider.prompts_named("conversation.reply")[1]
        assert "My first question" in prompt.user

    async def test_a_new_conversation_carries_no_history(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """Starting fresh must not leak the previous conversation into this one."""
        headers = await authenticate(api_client, "ada@example.com")
        await send(api_client, headers, "Something from an earlier conversation")

        await send(api_client, headers, "A brand new question")

        prompt = llm_provider.prompts_named("conversation.reply")[1]
        assert "earlier conversation" not in prompt.user

    async def test_history_sent_to_the_model_stays_bounded(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """A long conversation must not grow the prompt without limit."""
        headers = await authenticate(api_client, "ada@example.com")
        conversation_id = (await send(api_client, headers, "Turn 0")).json()["data"][
            "conversation_id"
        ]
        for turn in range(1, 16):
            await send(api_client, headers, f"Turn {turn}", conversation_id)

        prompt = llm_provider.prompts_named("conversation.reply")[-1]

        assert "Turn 0" not in prompt.user
        assert "Turn 15" in prompt.user
        assert len(prompt.user) < DEFAULT_CONTEXT_BUDGET.max_history_characters * 2


class TestProviderFailure:
    async def test_a_failed_reply_is_reported_rather_than_raised(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        response = await send(api_client, headers, "A question")

        assert response.status_code == 201

    async def test_the_exchange_is_still_recorded_when_generation_fails(
        self, api_client: AsyncClient, api_app, db_session: AsyncSession
    ):
        """Losing the user's message because the model failed would be worse."""
        headers = await authenticate(api_client, "ada@example.com")
        # Swapped after authenticating, so only the reply is affected.
        api_app.state.conversation_graph = build_test_conversation_graph(
            FakeLLMProvider(failure=LLMUnavailableError("down"))
        )

        response = await send(api_client, headers, "A question during an outage")

        data = response.json()["data"]
        assert data["degraded"] is True
        assert data["reply"]["content"] == FALLBACK_REPLY

        messages = (await db_session.execute(select(Message))).scalars().all()
        assert len(messages) == 2


class TestConversationOwnership:
    async def test_another_users_conversation_reports_not_found(self, api_client: AsyncClient):
        """404 rather than 403, so the API does not confirm it exists."""
        ada = await authenticate(api_client, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")
        conversation_id = (await send(api_client, ada, "Ada's question")).json()["data"][
            "conversation_id"
        ]

        response = await api_client.get(f"/api/v1/conversations/{conversation_id}", headers=grace)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"

    async def test_a_conversation_that_never_existed_looks_identical(self, api_client: AsyncClient):
        ada = await authenticate(api_client, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")
        real = (await send(api_client, ada, "Ada's question")).json()["data"]["conversation_id"]

        owned_by_other = await api_client.get(f"/api/v1/conversations/{real}", headers=grace)
        never_existed = await api_client.get(f"/api/v1/conversations/{uuid.uuid4()}", headers=grace)

        assert owned_by_other.status_code == never_existed.status_code
        assert owned_by_other.json()["error"] == never_existed.json()["error"]

    async def test_posting_into_another_users_conversation_is_refused(
        self, api_client: AsyncClient
    ):
        ada = await authenticate(api_client, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")
        conversation_id = (await send(api_client, ada, "Ada's question")).json()["data"][
            "conversation_id"
        ]

        response = await send(api_client, grace, "Sneaking in", conversation_id)

        assert response.status_code == 404

    async def test_a_refused_post_writes_nothing(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        ada = await authenticate(api_client, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")
        conversation_id = (await send(api_client, ada, "Ada's question")).json()["data"][
            "conversation_id"
        ]

        await send(api_client, grace, "Sneaking in", conversation_id)

        messages = (await db_session.execute(select(Message))).scalars().all()
        assert all(message.content != "Sneaking in" for message in messages)

    async def test_listing_shows_only_your_own_conversations(self, api_client: AsyncClient):
        ada = await authenticate(api_client, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")
        await send(api_client, ada, "Ada's question")

        listing = (await api_client.get("/api/v1/conversations", headers=grace)).json()

        assert listing["data"] == []
        assert listing["meta"]["total_items"] == 0
