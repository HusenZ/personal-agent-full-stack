import pytest
from httpx import AsyncClient

from tests.fakes import FakeLLMProvider

pytestmark = pytest.mark.integration

PASSWORD = "a-sufficiently-long-password"


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(replies=["A reply."])


async def authenticate(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    access_token: str = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


async def start_conversations(client: AsyncClient, headers: dict[str, str], count: int):
    ids = []
    for index in range(count):
        response = await client.post(
            "/api/v1/chat", headers=headers, json={"message": f"Question {index}"}
        )
        ids.append(response.json()["data"]["conversation_id"])
    return ids


class TestListing:
    async def test_an_empty_list_is_well_formed(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        body = (await api_client.get("/api/v1/conversations", headers=headers)).json()

        assert body["data"] == []
        assert body["meta"]["total_items"] == 0
        assert body["meta"]["total_pages"] == 0
        assert body["meta"]["has_next"] is False

    async def test_conversations_are_listed_most_recent_first(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await start_conversations(api_client, headers, 3)

        body = (await api_client.get("/api/v1/conversations", headers=headers)).json()

        assert [item["title"] for item in body["data"]] == [
            "Question 2",
            "Question 1",
            "Question 0",
        ]

    async def test_a_reply_moves_a_conversation_to_the_top(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        ids = await start_conversations(api_client, headers, 3)

        await api_client.post(
            "/api/v1/chat",
            headers=headers,
            json={"message": "A follow up", "conversation_id": ids[0]},
        )
        body = (await api_client.get("/api/v1/conversations", headers=headers)).json()

        assert body["data"][0]["id"] == ids[0]


class TestPagination:
    async def test_a_page_is_bounded(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await start_conversations(api_client, headers, 7)

        body = (
            await api_client.get("/api/v1/conversations?page=1&page_size=3", headers=headers)
        ).json()

        assert len(body["data"]) == 3
        assert body["meta"] == {
            "request_id": body["meta"]["request_id"],
            "page": 1,
            "page_size": 3,
            "total_items": 7,
            "total_pages": 3,
            "has_next": True,
        }

    async def test_pages_do_not_overlap(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await start_conversations(api_client, headers, 7)

        first = (
            await api_client.get("/api/v1/conversations?page=1&page_size=3", headers=headers)
        ).json()
        second = (
            await api_client.get("/api/v1/conversations?page=2&page_size=3", headers=headers)
        ).json()

        first_ids = {item["id"] for item in first["data"]}
        second_ids = {item["id"] for item in second["data"]}
        assert first_ids.isdisjoint(second_ids)

    async def test_the_final_page_reports_no_next(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await start_conversations(api_client, headers, 7)

        body = (
            await api_client.get("/api/v1/conversations?page=3&page_size=3", headers=headers)
        ).json()

        assert len(body["data"]) == 1
        assert body["meta"]["has_next"] is False

    async def test_a_page_beyond_the_end_is_empty_rather_than_an_error(
        self, api_client: AsyncClient
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await start_conversations(api_client, headers, 2)

        response = await api_client.get(
            "/api/v1/conversations?page=50&page_size=20", headers=headers
        )

        assert response.status_code == 200
        assert response.json()["data"] == []

    @pytest.mark.parametrize("query", ["page=0", "page_size=0", "page_size=101", "page=-1"])
    async def test_invalid_pagination_is_rejected(self, api_client: AsyncClient, query: str):
        """Without a ceiling on page_size a client could ask for every row at once."""
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.get(f"/api/v1/conversations?{query}", headers=headers)

        assert response.status_code == 422


class TestConversationDetail:
    async def test_a_conversation_is_returned_with_its_messages(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        conversation_id = (await start_conversations(api_client, headers, 1))[0]

        body = (
            await api_client.get(f"/api/v1/conversations/{conversation_id}", headers=headers)
        ).json()

        assert body["data"]["id"] == conversation_id
        assert body["data"]["title"] == "Question 0"
        assert [message["role"] for message in body["data"]["messages"]] == [
            "user",
            "assistant",
        ]

    async def test_the_detail_is_an_object_not_a_list(self, api_client: AsyncClient):
        """Pagination here applies to the messages, not to the conversation."""
        headers = await authenticate(api_client, "ada@example.com")
        conversation_id = (await start_conversations(api_client, headers, 1))[0]

        body = (
            await api_client.get(f"/api/v1/conversations/{conversation_id}", headers=headers)
        ).json()

        assert isinstance(body["data"], dict)

    async def test_messages_read_in_the_order_they_happened(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        conversation_id = (await start_conversations(api_client, headers, 1))[0]
        for turn in range(3):
            await api_client.post(
                "/api/v1/chat",
                headers=headers,
                json={"message": f"Follow up {turn}", "conversation_id": conversation_id},
            )

        body = (
            await api_client.get(
                f"/api/v1/conversations/{conversation_id}?page_size=100", headers=headers
            )
        ).json()

        user_messages = [
            message["content"] for message in body["data"]["messages"] if message["role"] == "user"
        ]
        assert user_messages == ["Question 0", "Follow up 0", "Follow up 1", "Follow up 2"]

    async def test_messages_are_paginated(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        conversation_id = (await start_conversations(api_client, headers, 1))[0]
        for turn in range(5):
            await api_client.post(
                "/api/v1/chat",
                headers=headers,
                json={"message": f"Follow up {turn}", "conversation_id": conversation_id},
            )

        body = (
            await api_client.get(
                f"/api/v1/conversations/{conversation_id}?page=1&page_size=4", headers=headers
            )
        ).json()

        assert len(body["data"]["messages"]) == 4
        assert body["meta"]["total_items"] == 12
        assert body["meta"]["has_next"] is True


class TestDeleting:
    async def test_a_deleted_conversation_and_its_messages_are_gone(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        (conversation_id,) = await start_conversations(api_client, headers, 1)

        response = await api_client.delete(
            f"/api/v1/conversations/{conversation_id}", headers=headers
        )

        assert response.status_code == 204
        read = await api_client.get(f"/api/v1/conversations/{conversation_id}", headers=headers)
        assert read.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
        listing = (await api_client.get("/api/v1/conversations", headers=headers)).json()
        assert listing["data"] == []

    async def test_another_users_conversation_cannot_be_deleted(self, api_client: AsyncClient):
        owner = await authenticate(api_client, "grace@example.com")
        (conversation_id,) = await start_conversations(api_client, owner, 1)
        intruder = await authenticate(api_client, "ada@example.com")

        response = await api_client.delete(
            f"/api/v1/conversations/{conversation_id}", headers=intruder
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
        still_there = await api_client.get(
            f"/api/v1/conversations/{conversation_id}", headers=owner
        )
        assert still_there.status_code == 200

    async def test_deleting_requires_a_token(self, api_client: AsyncClient):
        response = await api_client.delete(
            "/api/v1/conversations/00000000-0000-0000-0000-000000000000"
        )

        assert response.status_code == 401
