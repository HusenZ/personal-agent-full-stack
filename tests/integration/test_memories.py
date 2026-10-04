import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory.decisions import MemoryJudgment, MemoryVerdict, PairJudgment
from app.domain.memory.types import (
    MemoryCandidate,
    MemoryChange,
    MemoryDurability,
    MemoryExtraction,
    MemoryStatus,
    MemoryType,
)
from app.infrastructure.database.models import Memory, MemoryVersion
from tests.fakes import FakeLLMProvider

pytestmark = pytest.mark.integration

PASSWORD = "a-sufficiently-long-password"


def candidate(
    content: str,
    memory_type: MemoryType = MemoryType.GOAL,
    *,
    importance: float = 0.85,
    confidence: float = 0.9,
    durability: MemoryDurability = MemoryDurability.LONG_TERM,
    is_sensitive: bool = False,
) -> MemoryCandidate:
    return MemoryCandidate(
        content=content,
        memory_type=memory_type,
        durability=durability,
        importance=importance,
        confidence=confidence,
        is_sensitive=is_sensitive,
    )


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(replies=["Understood."])


async def authenticate(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    access_token: str = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


async def chat(client: AsyncClient, headers: dict[str, str], message: str) -> None:
    await client.post("/api/v1/chat", headers=headers, json={"message": message})


class TestAuthorization:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/v1/memories"),
            ("patch", f"/api/v1/memories/{uuid.uuid4()}"),
            ("delete", f"/api/v1/memories/{uuid.uuid4()}"),
        ],
    )
    async def test_every_endpoint_requires_a_token(
        self, api_client: AsyncClient, method: str, path: str
    ):
        response = await api_client.request(method, path, json={"content": "x"})

        assert response.status_code == 401


class TestExtractionFromChat:
    async def test_an_ordinary_exchange_remembers_nothing(self, api_client: AsyncClient):
        """The default is to keep nothing; most exchanges deserve that."""
        headers = await authenticate(api_client, "ada@example.com")

        await chat(api_client, headers, "Thanks, that helps.")

        body = (await api_client.get("/api/v1/memories", headers=headers)).json()
        assert body["data"] == []

    async def test_something_worth_keeping_is_remembered(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_memory_extractions(
            MemoryExtraction(candidates=[candidate("I want to move into AI engineering")])
        )

        await chat(api_client, headers, "I have decided to move into AI engineering.")

        body = (await api_client.get("/api/v1/memories", headers=headers)).json()
        assert [item["content"] for item in body["data"]] == ["I want to move into AI engineering"]
        assert body["data"][0]["memory_type"] == "goal"
        assert body["data"][0]["status"] == "active"

    async def test_a_weak_candidate_is_not_stored(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_memory_extractions(
            MemoryExtraction(
                candidates=[candidate("Possibly likes jazz", MemoryType.FACT, confidence=0.1)]
            )
        )

        await chat(api_client, headers, "I might like jazz, I am not sure.")

        body = (await api_client.get("/api/v1/memories", headers=headers)).json()
        assert body["data"] == []

    async def test_creation_is_recorded_in_the_history(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_memory_extractions(
            MemoryExtraction(candidates=[candidate("I want to move into AI engineering")])
        )

        await chat(api_client, headers, "I have decided to move into AI engineering.")

        versions = (await db_session.execute(select(MemoryVersion))).scalars().all()
        assert [version.change for version in versions] == [MemoryChange.CREATED]

    async def test_the_memory_is_linked_to_the_conversation_it_came_from(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_memory_extractions(
            MemoryExtraction(candidates=[candidate("I want to move into AI engineering")])
        )

        await chat(api_client, headers, "I have decided to move into AI engineering.")

        memory = (await db_session.execute(select(Memory))).scalar_one()
        assert memory.conversation_id is not None

    async def test_temporary_context_is_given_an_expiry(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """Otherwise "I am on a train" would still be shaping answers next month."""
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_memory_extractions(
            MemoryExtraction(
                candidates=[
                    candidate(
                        "I am travelling this week",
                        MemoryType.TEMPORARY_CONTEXT,
                        durability=MemoryDurability.LONG_TERM,
                    )
                ]
            )
        )

        await chat(api_client, headers, "I am travelling this week.")

        memory = (await db_session.execute(select(Memory))).scalar_one()
        assert memory.expires_at is not None
        assert memory.durability is MemoryDurability.TEMPORARY

    async def test_a_lasting_memory_has_no_expiry(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_memory_extractions(
            MemoryExtraction(candidates=[candidate("I want to move into AI engineering")])
        )

        await chat(api_client, headers, "I have decided to move into AI engineering.")

        memory = (await db_session.execute(select(Memory))).scalar_one()
        assert memory.expires_at is None

    async def test_sensitivity_is_recorded_and_surfaced(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_memory_extractions(
            MemoryExtraction(
                candidates=[
                    candidate("Recovering from an injury", MemoryType.FACT, is_sensitive=True)
                ]
            )
        )

        await chat(api_client, headers, "I am recovering from an injury.")

        body = (await api_client.get("/api/v1/memories", headers=headers)).json()
        assert body["data"][0]["is_sensitive"] is True


class TestLifecycle:
    async def test_a_restatement_reinforces_rather_than_duplicating(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_memory_extractions(
            MemoryExtraction(
                candidates=[candidate("I want to move into AI engineering", importance=0.6)]
            ),
            MemoryExtraction(
                candidates=[candidate("I want to move into AI engineering", importance=0.95)]
            ),
        )

        await chat(api_client, headers, "I want to move into AI engineering.")
        await chat(api_client, headers, "Again: I want to move into AI engineering.")

        memories = (await db_session.execute(select(Memory))).scalars().all()
        assert len(memories) == 1
        assert memories[0].importance == 0.95

    async def test_reinforcement_is_recorded_in_the_history(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_memory_extractions(
            MemoryExtraction(candidates=[candidate("I want to move into AI engineering")]),
            MemoryExtraction(candidates=[candidate("I want to move into AI engineering")]),
        )

        await chat(api_client, headers, "One.")
        await chat(api_client, headers, "Two.")

        versions = (await db_session.execute(select(MemoryVersion))).scalars().all()
        assert {version.change for version in versions} == {
            MemoryChange.CREATED,
            MemoryChange.REINFORCED,
        }

    async def test_a_contradiction_supersedes_the_old_belief(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_memory_extractions(
            MemoryExtraction(
                candidates=[candidate("I have decided to stay in mobile development")]
            ),
            MemoryExtraction(candidates=[candidate("I want to move into AI engineering")]),
        )

        await chat(api_client, headers, "I am staying in mobile development.")

        first = (await db_session.execute(select(Memory))).scalar_one()
        llm_provider.queue_judgments(
            MemoryJudgment(
                judgments=[PairJudgment(memory_id=str(first.id), verdict=MemoryVerdict.CONTRADICTS)]
            )
        )

        await chat(api_client, headers, "Actually I want to move into AI engineering.")

        # Expire so the query doesn't return the stale cached copy.
        db_session.expire_all()
        memories = (
            (await db_session.execute(select(Memory).order_by(Memory.created_at))).scalars().all()
        )
        assert len(memories) == 2
        assert memories[0].status is MemoryStatus.SUPERSEDED
        assert memories[1].status is MemoryStatus.ACTIVE
        assert memories[0].superseded_by_id == memories[1].id

    async def test_a_superseded_memory_is_hidden_by_default(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_memory_extractions(
            MemoryExtraction(
                candidates=[candidate("I have decided to stay in mobile development")]
            ),
            MemoryExtraction(candidates=[candidate("I want to move into AI engineering")]),
        )
        await chat(api_client, headers, "Staying in mobile.")
        first = (await db_session.execute(select(Memory))).scalar_one()
        llm_provider.queue_judgments(
            MemoryJudgment(
                judgments=[PairJudgment(memory_id=str(first.id), verdict=MemoryVerdict.CONTRADICTS)]
            )
        )
        await chat(api_client, headers, "Moving to AI.")

        default = (await api_client.get("/api/v1/memories", headers=headers)).json()
        everything = (
            await api_client.get("/api/v1/memories?include_superseded=true", headers=headers)
        ).json()

        assert len(default["data"]) == 1
        assert len(everything["data"]) == 2

    async def test_history_is_preserved_when_a_belief_changes(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """The point of superseding rather than overwriting is that what the
        user used to believe stays answerable."""
        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_memory_extractions(
            MemoryExtraction(
                candidates=[candidate("I have decided to stay in mobile development")]
            ),
            MemoryExtraction(candidates=[candidate("I want to move into AI engineering")]),
        )
        await chat(api_client, headers, "Staying in mobile.")
        first = (await db_session.execute(select(Memory))).scalar_one()
        llm_provider.queue_judgments(
            MemoryJudgment(
                judgments=[PairJudgment(memory_id=str(first.id), verdict=MemoryVerdict.CONTRADICTS)]
            )
        )
        await chat(api_client, headers, "Moving to AI.")

        body = (
            await api_client.get("/api/v1/memories?include_superseded=true", headers=headers)
        ).json()

        superseded = [item for item in body["data"] if item["status"] == "superseded"]
        assert superseded[0]["content"] == "I have decided to stay in mobile development"
        assert superseded[0]["superseded_by_id"] is not None


class TestUserManagement:
    async def _one_memory(self, client: AsyncClient, provider: FakeLLMProvider, email: str):
        headers = await authenticate(client, email)
        provider.queue_memory_extractions(
            MemoryExtraction(candidates=[candidate("I want to move into AI engineering")])
        )
        await chat(client, headers, "Moving to AI.")
        body = (await client.get("/api/v1/memories", headers=headers)).json()
        return headers, body["data"][0]["id"]

    async def test_a_memory_can_be_corrected(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers, memory_id = await self._one_memory(api_client, llm_provider, "ada@example.com")

        response = await api_client.patch(
            f"/api/v1/memories/{memory_id}",
            headers=headers,
            json={"content": "I want to move into AI research, not engineering"},
        )

        assert response.status_code == 200
        assert response.json()["data"]["content"] == (
            "I want to move into AI research, not engineering"
        )

    async def test_an_edit_is_recorded_in_the_history(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers, memory_id = await self._one_memory(api_client, llm_provider, "ada@example.com")

        await api_client.patch(
            f"/api/v1/memories/{memory_id}", headers=headers, json={"content": "Corrected"}
        )

        versions = (await db_session.execute(select(MemoryVersion))).scalars().all()
        assert MemoryChange.UPDATED in {version.change for version in versions}

    async def test_a_memory_can_be_archived(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers, memory_id = await self._one_memory(api_client, llm_provider, "ada@example.com")

        await api_client.patch(
            f"/api/v1/memories/{memory_id}", headers=headers, json={"status": "archived"}
        )
        default = (await api_client.get("/api/v1/memories", headers=headers)).json()

        assert default["data"] == []

    async def test_supersession_cannot_be_asserted_by_a_client(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """It is a conclusion the system draws from a contradiction, not a claim."""
        headers, memory_id = await self._one_memory(api_client, llm_provider, "ada@example.com")

        response = await api_client.patch(
            f"/api/v1/memories/{memory_id}", headers=headers, json={"status": "superseded"}
        )

        assert response.status_code == 422

    async def test_an_empty_update_is_rejected(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers, memory_id = await self._one_memory(api_client, llm_provider, "ada@example.com")

        response = await api_client.patch(f"/api/v1/memories/{memory_id}", headers=headers, json={})

        assert response.status_code == 422

    async def test_deleting_actually_removes_the_content(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """A tombstone still holding the text would not be a deletion."""
        headers, memory_id = await self._one_memory(api_client, llm_provider, "ada@example.com")

        response = await api_client.delete(f"/api/v1/memories/{memory_id}", headers=headers)

        assert response.status_code == 204
        assert (await db_session.execute(select(Memory))).scalars().all() == []

    async def test_deleting_removes_its_history_too(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers, memory_id = await self._one_memory(api_client, llm_provider, "ada@example.com")

        await api_client.delete(f"/api/v1/memories/{memory_id}", headers=headers)

        assert (await db_session.execute(select(MemoryVersion))).scalars().all() == []


class TestUserIsolation:
    async def _one_memory(self, client: AsyncClient, provider: FakeLLMProvider, email: str):
        headers = await authenticate(client, email)
        provider.queue_memory_extractions(
            MemoryExtraction(candidates=[candidate(f"A private goal for {email}")])
        )
        await chat(client, headers, "A goal.")
        body = (await client.get("/api/v1/memories", headers=headers)).json()
        return headers, body["data"][0]["id"]

    async def test_one_users_memories_are_invisible_to_another(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        _ada, _ = await self._one_memory(api_client, llm_provider, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")

        body = (await api_client.get("/api/v1/memories", headers=grace)).json()

        assert body["data"] == []

    async def test_another_users_memory_reports_not_found(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """404 rather than 403, so the API does not confirm it exists."""
        _, memory_id = await self._one_memory(api_client, llm_provider, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")

        owned_by_other = await api_client.patch(
            f"/api/v1/memories/{memory_id}", headers=grace, json={"content": "Hijacked"}
        )
        never_existed = await api_client.patch(
            f"/api/v1/memories/{uuid.uuid4()}", headers=grace, json={"content": "Hijacked"}
        )

        assert owned_by_other.status_code == 404
        assert owned_by_other.json()["error"] == never_existed.json()["error"]

    async def test_another_users_memory_cannot_be_deleted(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        _, memory_id = await self._one_memory(api_client, llm_provider, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")

        response = await api_client.delete(f"/api/v1/memories/{memory_id}", headers=grace)

        assert response.status_code == 404
        assert len((await db_session.execute(select(Memory))).scalars().all()) == 1

    async def test_extraction_only_compares_against_your_own_memories(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        _ada, _ = await self._one_memory(api_client, llm_provider, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")
        llm_provider.queue_memory_extractions(
            MemoryExtraction(candidates=[candidate("Grace's own goal")])
        )

        await chat(api_client, grace, "My goal.")

        judgement_prompts = llm_provider.prompts_named("memory.judgement")
        assert all("A private goal for ada@example.com" not in p.user for p in judgement_prompts)
