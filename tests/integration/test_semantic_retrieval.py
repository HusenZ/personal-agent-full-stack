import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory.types import (
    MemoryCandidate,
    MemoryDurability,
    MemoryExtraction,
    MemoryStatus,
    MemoryType,
)
from app.infrastructure.database.models import Memory
from app.infrastructure.llm.exceptions import LLMUnavailableError
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider
from tests.integration.conftest import sessions_like

pytestmark = pytest.mark.integration

PASSWORD = "a-sufficiently-long-password"


def candidate(
    content: str, memory_type: MemoryType = MemoryType.FACT, *, importance: float = 0.8
) -> MemoryCandidate:
    return MemoryCandidate(
        content=content,
        memory_type=memory_type,
        durability=MemoryDurability.LONG_TERM,
        importance=importance,
        confidence=0.9,
    )


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(replies=["Understood."])


async def authenticate(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    access_token: str = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


RECALL_HEADING = "Things you remember this user telling you before:"


def recalled_section(prompt_user: str) -> str:
    """Just the recalled-memories part of a prompt.

    Asserting against the whole prompt would be meaningless: the user's own
    question is in there too, and it usually contains the same words as the
    memory being looked for.
    """
    if RECALL_HEADING not in prompt_user:
        return ""

    start = prompt_user.index(RECALL_HEADING)
    remainder = prompt_user[start:]
    end = remainder.find("Earlier in this conversation")
    if end == -1:
        end = remainder.find("The message to answer:")

    return remainder if end == -1 else remainder[:end]


async def chat(client: AsyncClient, headers: dict[str, str], message: str) -> None:
    await client.post("/api/v1/chat", headers=headers, json={"message": message})


async def remember(
    client: AsyncClient,
    provider: FakeLLMProvider,
    headers: dict[str, str],
    *candidates: MemoryCandidate,
) -> None:
    provider.queue_memory_extractions(MemoryExtraction(candidates=list(candidates)))
    await chat(client, headers, "Something worth remembering.")
    provider.queue_memory_extractions(MemoryExtraction())


class TestEmbeddingOnWrite:
    async def test_a_remembered_memory_is_embedded(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await remember(api_client, llm_provider, headers, candidate("I play the cello"))

        memory = (await db_session.execute(select(Memory))).scalar_one()

        assert memory.embedding is not None
        assert len(memory.embedding) == 1536
        assert memory.embedding_model == "test-embedding-model"

    async def test_an_embedding_failure_still_keeps_the_memory(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        embedding_provider: FakeEmbeddingProvider,
        db_session: AsyncSession,
    ):
        """Losing what the user told us because a vector call failed is worse
        than holding it without being able to search it yet."""
        headers = await authenticate(api_client, "ada@example.com")
        embedding_provider._failure = LLMUnavailableError("embeddings down")

        await remember(api_client, llm_provider, headers, candidate("I play the cello"))

        memory = (await db_session.execute(select(Memory))).scalar_one()
        assert memory.embedding is None
        assert memory.embedding_model is None

    async def test_editing_a_memory_re_embeds_it(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """A stale vector would make the memory findable by a meaning it no
        longer has."""
        headers = await authenticate(api_client, "ada@example.com")
        await remember(api_client, llm_provider, headers, candidate("I play the cello"))
        memory_id = (await api_client.get("/api/v1/memories", headers=headers)).json()["data"][0][
            "id"
        ]
        before = (await db_session.execute(select(Memory))).scalar_one().embedding

        await api_client.patch(
            f"/api/v1/memories/{memory_id}",
            headers=headers,
            json={"content": "I play the double bass in an orchestra"},
        )

        db_session.expire_all()
        after = (await db_session.execute(select(Memory))).scalar_one().embedding
        assert before is not None and after is not None
        assert list(after) != list(before)


class TestRetrievalInConversation:
    async def test_a_relevant_memory_reaches_the_prompt(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await remember(
            api_client,
            llm_provider,
            headers,
            candidate("I play the cello in a string quartet"),
        )

        await chat(api_client, headers, "Any advice about my cello quartet rehearsals?")

        prompt = llm_provider.prompts_named("conversation.reply")[-1]
        assert "cello in a string quartet" in recalled_section(prompt.user)

    async def test_an_unrelated_memory_stays_out_of_the_prompt(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """More context is not better context; irrelevant recall is noise."""
        headers = await authenticate(api_client, "ada@example.com")
        await remember(
            api_client, llm_provider, headers, candidate("I play the cello in a string quartet")
        )

        await chat(api_client, headers, "How do I configure a database connection pool?")

        prompt = llm_provider.prompts_named("conversation.reply")[-1]
        assert "cello" not in recalled_section(prompt.user)

    async def test_recalled_memories_are_fenced_as_untrusted(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await remember(
            api_client, llm_provider, headers, candidate("I play the cello in a string quartet")
        )

        await chat(api_client, headers, "Advice about my cello quartet?")

        prompt = llm_provider.prompts_named("conversation.reply")[-1]
        assert "BEGIN_UNTRUSTED_CONTENT" in recalled_section(prompt.user)

    async def test_a_superseded_memory_is_never_recalled(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """Recalling a belief the user has replaced is worse than recalling none."""
        headers = await authenticate(api_client, "ada@example.com")
        await remember(
            api_client, llm_provider, headers, candidate("I play the cello in a string quartet")
        )
        await db_session.execute(update(Memory).values(status=MemoryStatus.SUPERSEDED))
        await db_session.commit()

        await chat(api_client, headers, "Advice about my cello quartet?")

        prompt = llm_provider.prompts_named("conversation.reply")[-1]
        assert "cello" not in recalled_section(prompt.user)

    async def test_an_expired_memory_is_never_recalled(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        from datetime import UTC, datetime, timedelta

        headers = await authenticate(api_client, "ada@example.com")
        await remember(
            api_client, llm_provider, headers, candidate("I play the cello in a string quartet")
        )
        await db_session.execute(
            update(Memory).values(expires_at=datetime.now(tz=UTC) - timedelta(days=1))
        )
        await db_session.commit()

        await chat(api_client, headers, "Advice about my cello quartet?")

        prompt = llm_provider.prompts_named("conversation.reply")[-1]
        assert "cello" not in recalled_section(prompt.user)

    async def test_a_vector_from_another_model_is_never_compared(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
        embedding_provider: FakeEmbeddingProvider,
    ):
        """Distances across embedding models look plausible and mean nothing, so
        a stale vector must never produce a similarity. The memory itself is still
        the user's and still findable by its words - that is keyword search doing
        its job, not the invariant failing."""
        from app.retrieval.hybrid import HybridMemoryRetriever
        from app.retrieval.keyword import KeywordMemoryRetriever
        from app.retrieval.semantic import SemanticMemoryRetriever
        from app.retrieval.types import RetrievalSource

        headers = await authenticate(api_client, "ada@example.com")
        await remember(
            api_client, llm_provider, headers, candidate("I play the cello in a string quartet")
        )
        await db_session.execute(update(Memory).values(embedding_model="some-older-model"))
        await db_session.commit()
        memory = (await db_session.execute(select(Memory))).scalar_one()

        retriever = HybridMemoryRetriever(
            SemanticMemoryRetriever(sessions_like(db_session)),
            KeywordMemoryRetriever(sessions_like(db_session)),
            embedding_model="test-embedding-model",
        )
        query = "Advice about my cello quartet?"
        results = await retriever.retrieve(
            memory.user_id, query, await embedding_provider.embed_query(query)
        )

        assert [result.sources for result in results] == [(RetrievalSource.KEYWORD,)]
        assert results[0].similarity is None

    async def test_how_many_memories_are_recalled_is_bounded(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await remember(
            api_client,
            llm_provider,
            headers,
            *[candidate(f"I play the cello piece number {index}") for index in range(12)],
        )

        await chat(api_client, headers, "Advice about my cello pieces?")

        prompt = llm_provider.prompts_named("conversation.reply")[-1]
        assert recalled_section(prompt.user).count("I play the cello piece") <= 6

    async def test_retrieval_failing_does_not_fail_the_reply(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        embedding_provider: FakeEmbeddingProvider,
    ):
        """Retrieval failing costs personalisation, not the answer."""
        headers = await authenticate(api_client, "ada@example.com")
        embedding_provider._failure = LLMUnavailableError("embeddings down")

        response = await api_client.post(
            "/api/v1/chat", headers=headers, json={"message": "Any advice?"}
        )

        assert response.status_code == 201
        assert response.json()["data"]["degraded"] is False


class TestUserIsolation:
    async def test_another_users_memory_is_never_recalled(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        ada = await authenticate(api_client, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")
        await remember(
            api_client, llm_provider, ada, candidate("I play the cello in a string quartet")
        )

        await chat(api_client, grace, "Any advice about my cello quartet rehearsals?")

        prompt = llm_provider.prompts_named("conversation.reply")[-1]
        assert "cello" not in recalled_section(prompt.user)
