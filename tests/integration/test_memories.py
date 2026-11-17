import asyncio
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
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
from app.infrastructure.database.models import Event, Memory, MemoryVersion
from app.schemas.pagination import MAX_PAGE
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider

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


# --- Edge cases and error handling ------------------------------------------------

MEMORIES = "/api/v1/memories"


async def remember(
    client: AsyncClient,
    provider: FakeLLMProvider,
    headers: dict[str, str],
    *candidates: MemoryCandidate,
) -> list[dict[str, Any]]:
    """Store the given memories through one chat turn; returns them as listed."""
    provider.queue_memory_extractions(MemoryExtraction(candidates=list(candidates)))
    await chat(client, headers, "Some things about me.")
    query = "?include_superseded=true&include_archived=true&page_size=100"
    listed: list[dict[str, Any]] = (await client.get(MEMORIES + query, headers=headers)).json()[
        "data"
    ]
    return listed


async def one_memory(
    client: AsyncClient, provider: FakeLLMProvider, email: str = "ada@example.com"
) -> tuple[dict[str, str], dict[str, Any]]:
    headers = await authenticate(client, email)
    [memory] = await remember(client, provider, headers, candidate("I want to move into AI"))
    return headers, memory


async def superseded_pair(
    client: AsyncClient, provider: FakeLLMProvider, db_session: AsyncSession
) -> tuple[dict[str, str], dict[str, Any], dict[str, Any]]:
    """An old belief and the newer memory that contradicted it."""
    headers = await authenticate(client, "ada@example.com")
    provider.queue_memory_extractions(
        MemoryExtraction(candidates=[candidate("I have decided to stay in mobile development")]),
        MemoryExtraction(candidates=[candidate("I want to move into AI engineering")]),
    )
    await chat(client, headers, "I am staying in mobile development.")
    first = (await db_session.execute(select(Memory))).scalar_one()
    provider.queue_judgments(
        MemoryJudgment(
            judgments=[PairJudgment(memory_id=str(first.id), verdict=MemoryVerdict.CONTRADICTS)]
        )
    )
    await chat(client, headers, "Actually I want to move into AI engineering.")

    body = (await client.get(f"{MEMORIES}?include_superseded=true", headers=headers)).json()
    old = next(item for item in body["data"] if item["status"] == "superseded")
    new = next(item for item in body["data"] if item["status"] == "active")
    return headers, old, new


def assert_validation_error(response: Any, field: str) -> None:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert field in {detail["field"] for detail in body["error"]["details"]}
    assert body["meta"]["request_id"]


class TestListingEdgeCases:
    async def test_an_empty_list_is_well_formed(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        body = (await api_client.get(MEMORIES, headers=headers)).json()

        assert body["data"] == []
        assert body["meta"]["total_items"] == 0
        assert body["meta"]["total_pages"] == 0
        assert body["meta"]["has_next"] is False

    async def test_pages_split_the_list_without_overlap_or_gaps(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        stored = await remember(
            api_client,
            llm_provider,
            headers,
            candidate("Wants to learn the cello"),
            candidate("Prefers working in the morning", MemoryType.PREFERENCE),
            candidate("Lives near the coast", MemoryType.FACT),
        )

        first = (await api_client.get(f"{MEMORIES}?page_size=2", headers=headers)).json()
        second = (await api_client.get(f"{MEMORIES}?page_size=2&page=2", headers=headers)).json()

        assert len(first["data"]) == 2
        assert first["meta"]["has_next"] is True
        assert first["meta"]["total_pages"] == 2
        assert len(second["data"]) == 1
        assert second["meta"]["has_next"] is False
        paged = [item["id"] for item in first["data"] + second["data"]]
        assert sorted(paged) == sorted(item["id"] for item in stored)

    async def test_a_page_past_the_end_is_empty_not_an_error(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers, _ = await one_memory(api_client, llm_provider)

        response = await api_client.get(f"{MEMORIES}?page=50", headers=headers)

        assert response.status_code == 200
        assert response.json()["data"] == []
        assert response.json()["meta"]["total_items"] == 1
        assert response.json()["meta"]["has_next"] is False

    async def test_the_largest_allowed_page_is_answered(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.get(
            f"{MEMORIES}?page={MAX_PAGE}&page_size=100", headers=headers
        )

        assert response.status_code == 200
        assert response.json()["data"] == []

    @pytest.mark.parametrize("page", [MAX_PAGE + 1, 10**20])
    async def test_an_absurd_page_is_a_validation_error_not_a_crash(
        self, api_client: AsyncClient, page: int
    ):
        """Unbounded, the implied offset overflowed Postgres's bigint and the API answered 500."""
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.get(f"{MEMORIES}?page={page}", headers=headers)

        assert_validation_error(response, "query.page")

    @pytest.mark.parametrize(
        ("query", "field"),
        [
            ("page=0", "query.page"),
            ("page=-1", "query.page"),
            ("page=first", "query.page"),
            ("page_size=0", "query.page_size"),
            ("page_size=101", "query.page_size"),
            ("memory_type=feeling", "query.memory_type"),
            ("include_superseded=maybe", "query.include_superseded"),
            ("include_archived=2", "query.include_archived"),
        ],
    )
    async def test_invalid_query_parameters_are_rejected_by_name(
        self, api_client: AsyncClient, query: str, field: str
    ):
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.get(f"{MEMORIES}?{query}", headers=headers)

        assert_validation_error(response, field)

    async def test_filters_combine_and_totals_count_only_what_matches(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        stored = await remember(
            api_client,
            llm_provider,
            headers,
            candidate("Wants to learn the cello"),
            candidate("Wants to run a marathon"),
            candidate("Lives near the coast", MemoryType.FACT),
        )
        cello = next(item for item in stored if item["content"] == "Wants to learn the cello")
        await api_client.patch(
            f"{MEMORIES}/{cello['id']}", headers=headers, json={"status": "archived"}
        )

        goals = (await api_client.get(f"{MEMORIES}?memory_type=goal", headers=headers)).json()
        with_archived = (
            await api_client.get(
                f"{MEMORIES}?memory_type=goal&include_archived=true", headers=headers
            )
        ).json()

        assert [item["content"] for item in goals["data"]] == ["Wants to run a marathon"]
        assert goals["meta"]["total_items"] == 1
        assert {item["content"] for item in with_archived["data"]} == {
            "Wants to learn the cello",
            "Wants to run a marathon",
        }
        assert with_archived["meta"]["total_items"] == 2


class TestUpdateValidation:
    @pytest.mark.parametrize(
        ("payload", "field"),
        [
            ({"content": ""}, "body.content"),
            ({"content": "   \n\t "}, "body.content"),
            ({"content": "a" * 1001}, "body.content"),
            ({"content": 42}, "body.content"),
            ({"importance": -0.01}, "body.importance"),
            ({"importance": 1.01}, "body.importance"),
            ({"importance": "high"}, "body.importance"),
            ({"status": "deleted"}, "body.status"),
            ({"status": "superseded"}, "body"),
            ({"user_id": str(uuid.uuid4())}, "body"),
            ({"content": None, "importance": None, "status": None}, "body"),
            (["not", "an", "object"], "body"),
        ],
    )
    async def test_invalid_updates_are_rejected_by_field(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        payload: Any,
        field: str,
    ):
        headers, memory = await one_memory(api_client, llm_provider)

        response = await api_client.patch(
            f"{MEMORIES}/{memory['id']}", headers=headers, json=payload
        )

        assert_validation_error(response, field)

    async def test_a_nul_character_is_a_validation_error_not_a_crash(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """Valid JSON that Postgres cannot store; it used to reach the write and answer 500."""
        headers, memory = await one_memory(api_client, llm_provider)

        response = await api_client.patch(
            f"{MEMORIES}/{memory['id']}", headers=headers, json={"content": "half\u0000way"}
        )

        assert_validation_error(response, "body.content")

    @pytest.mark.parametrize(
        "raw", ['{"importance": NaN}', '{"importance": Infinity}', '{"content": ', "not json"]
    )
    async def test_non_standard_or_broken_json_is_rejected(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, raw: str
    ):
        headers, memory = await one_memory(api_client, llm_provider)

        response = await api_client.patch(
            f"{MEMORIES}/{memory['id']}",
            headers={**headers, "Content-Type": "application/json"},
            content=raw,
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_a_missing_body_is_rejected(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers, memory = await one_memory(api_client, llm_provider)

        response = await api_client.patch(f"{MEMORIES}/{memory['id']}", headers=headers)

        assert_validation_error(response, "body")

    @pytest.mark.parametrize("method", ["patch", "delete"])
    async def test_a_malformed_id_is_a_validation_error(self, api_client: AsyncClient, method: str):
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.request(
            method, f"{MEMORIES}/not-a-uuid", headers=headers, json={"content": "x"}
        )

        assert_validation_error(response, "path.memory_id")

    async def test_a_rejected_update_changes_nothing(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers, memory = await one_memory(api_client, llm_provider)
        versions_before = await db_session.scalar(select(func.count()).select_from(MemoryVersion))

        await api_client.patch(
            f"{MEMORIES}/{memory['id']}", headers=headers, json={"content": "x", "importance": 7}
        )

        [after] = (await api_client.get(MEMORIES, headers=headers)).json()["data"]
        assert after == memory
        versions_after = await db_session.scalar(select(func.count()).select_from(MemoryVersion))
        assert versions_after == versions_before

    async def test_the_boundaries_themselves_are_accepted(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers, memory = await one_memory(api_client, llm_provider)
        url = f"{MEMORIES}/{memory['id']}"

        longest = await api_client.patch(url, headers=headers, json={"content": "a" * 1000})
        lowest = await api_client.patch(url, headers=headers, json={"importance": 0})
        highest = await api_client.patch(url, headers=headers, json={"importance": 1})

        assert longest.status_code == 200
        assert len(longest.json()["data"]["content"]) == 1000
        assert lowest.json()["data"]["importance"] == 0
        assert highest.json()["data"]["importance"] == 1

    async def test_surrounding_whitespace_is_trimmed(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers, memory = await one_memory(api_client, llm_provider)

        response = await api_client.patch(
            f"{MEMORIES}/{memory['id']}", headers=headers, json={"content": "  Corrected \n"}
        )

        assert response.json()["data"]["content"] == "Corrected"

    async def test_fields_a_client_may_not_set_are_ignored(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """Extra fields are not an error, but they must never be applied."""
        headers, memory = await one_memory(api_client, llm_provider)

        response = await api_client.patch(
            f"{MEMORIES}/{memory['id']}",
            headers=headers,
            json={
                "content": "Corrected",
                "memory_type": "fact",
                "is_sensitive": True,
                "superseded_by_id": str(uuid.uuid4()),
                "user_id": str(uuid.uuid4()),
            },
        )

        data = response.json()["data"]
        assert response.status_code == 200
        assert data["content"] == "Corrected"
        assert (data["memory_type"], data["is_sensitive"], data["superseded_by_id"]) == (
            memory["memory_type"],
            memory["is_sensitive"],
            None,
        )
        assert (await api_client.get(MEMORIES, headers=headers)).json()["data"][0]["id"] == (
            memory["id"]
        )


class TestRestoring:
    async def test_an_archived_memory_can_be_restored(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers, memory = await one_memory(api_client, llm_provider)
        url = f"{MEMORIES}/{memory['id']}"
        await api_client.patch(url, headers=headers, json={"status": "archived"})

        response = await api_client.patch(url, headers=headers, json={"status": "active"})

        assert response.json()["data"]["status"] == "active"
        listed = (await api_client.get(MEMORIES, headers=headers)).json()["data"]
        assert [item["id"] for item in listed] == [memory["id"]]

    async def test_restoring_a_superseded_memory_drops_its_replacement_link(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """An active memory that still names a replacement would contradict itself."""
        headers, old, new = await superseded_pair(api_client, llm_provider, db_session)

        response = await api_client.patch(
            f"{MEMORIES}/{old['id']}", headers=headers, json={"status": "active"}
        )

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "active"
        assert response.json()["data"]["superseded_by_id"] is None
        listed = (await api_client.get(MEMORIES, headers=headers)).json()["data"]
        assert {item["id"] for item in listed} == {old["id"], new["id"]}

    async def test_archiving_a_superseded_memory_keeps_its_replacement_link(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers, old, new = await superseded_pair(api_client, llm_provider, db_session)

        response = await api_client.patch(
            f"{MEMORIES}/{old['id']}", headers=headers, json={"status": "archived"}
        )

        assert response.json()["data"]["superseded_by_id"] == new["id"]

    async def test_deleting_a_replacement_leaves_the_old_belief_superseded_and_restorable(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """Deleting the newer memory is not a restatement of the old one, so it stays hidden."""
        headers, old, new = await superseded_pair(api_client, llm_provider, db_session)

        deleted = await api_client.delete(f"{MEMORIES}/{new['id']}", headers=headers)
        hidden = (await api_client.get(MEMORIES, headers=headers)).json()["data"]
        restored = await api_client.patch(
            f"{MEMORIES}/{old['id']}", headers=headers, json={"status": "active"}
        )

        assert deleted.status_code == 204
        assert hidden == []
        assert restored.json()["data"]["status"] == "active"


class TestDeletionEdgeCases:
    async def test_deleting_twice_reports_not_found_the_second_time(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers, memory = await one_memory(api_client, llm_provider)
        url = f"{MEMORIES}/{memory['id']}"

        first = await api_client.delete(url, headers=headers)
        second = await api_client.delete(url, headers=headers)

        assert first.status_code == 204
        assert first.content == b""
        assert second.status_code == 404
        assert second.json()["error"] == {
            "code": "MEMORY_NOT_FOUND",
            "message": "Memory was not found.",
        }
        assert second.json()["meta"]["request_id"]

    async def test_a_deleted_memory_cannot_be_edited(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers, memory = await one_memory(api_client, llm_provider)
        await api_client.delete(f"{MEMORIES}/{memory['id']}", headers=headers)

        response = await api_client.patch(
            f"{MEMORIES}/{memory['id']}", headers=headers, json={"content": "Back again"}
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "MEMORY_NOT_FOUND"

    async def test_deleting_a_memory_deletes_the_events_that_repeat_its_text(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """An event's description is the memory's text; left behind, the words would survive."""
        headers = await authenticate(api_client, "ada@example.com")
        [memory] = await remember(
            api_client,
            llm_provider,
            headers,
            candidate(
                "Studied for two hours after a hard therapy session",
                MemoryType.EVENT,
                durability=MemoryDurability.SHORT_TERM,
            ).model_copy(update={"activity": "study", "quantity": 2, "unit": "hours"}),
        )
        assert len((await db_session.execute(select(Event))).scalars().all()) == 1

        await api_client.delete(f"{MEMORIES}/{memory['id']}", headers=headers)

        db_session.expire_all()
        assert (await db_session.execute(select(Event))).scalars().all() == []

    async def test_deleting_one_memory_leaves_other_events_alone(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        study = candidate(
            "Studied for two hours", MemoryType.EVENT, durability=MemoryDurability.SHORT_TERM
        ).model_copy(update={"activity": "study", "quantity": 2, "unit": "hours"})
        run = candidate(
            "Ran five kilometres", MemoryType.EVENT, durability=MemoryDurability.SHORT_TERM
        ).model_copy(update={"activity": "run", "quantity": 5, "unit": "km"})
        stored = await remember(api_client, llm_provider, headers, study, run)
        studied = next(item for item in stored if item["content"] == "Studied for two hours")

        await api_client.delete(f"{MEMORIES}/{studied['id']}", headers=headers)

        db_session.expire_all()
        remaining = (await db_session.execute(select(Event))).scalars().all()
        assert [event.activity for event in remaining] == ["run"]


class GatedEmbeddings(FakeEmbeddingProvider):
    """Holds an embedding call open until released, to widen a race on purpose."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.gated = False

    async def embed_query(self, text: str) -> list[float]:
        if self.gated:
            self.gated = False
            self.entered.set()
            await self.release.wait()
        return await super().embed_query(text)


class TestConcurrentChanges:
    @pytest.fixture
    def embedding_provider(self) -> GatedEmbeddings:
        return GatedEmbeddings()

    async def test_an_edit_that_loses_a_race_with_a_delete_reports_not_found(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        embedding_provider: GatedEmbeddings,
        db_session: AsyncSession,
    ):
        """An edit waits on the embedder between reading and writing; a delete can land there.

        It used to fail with a 500 from the UPDATE matching no row.
        """
        headers, memory = await one_memory(api_client, llm_provider)
        url = f"{MEMORIES}/{memory['id']}"
        embedding_provider.gated = True

        edit = asyncio.create_task(
            api_client.patch(url, headers=headers, json={"content": "Edited"})
        )
        await asyncio.wait_for(embedding_provider.entered.wait(), timeout=5)
        deleted = await api_client.delete(url, headers=headers)
        embedding_provider.release.set()
        edited = await asyncio.wait_for(edit, timeout=5)

        assert deleted.status_code == 204
        assert edited.status_code == 404
        assert edited.json()["error"]["code"] == "MEMORY_NOT_FOUND"
        assert (await db_session.execute(select(Memory))).scalars().all() == []
        assert (await db_session.execute(select(MemoryVersion))).scalars().all() == []
