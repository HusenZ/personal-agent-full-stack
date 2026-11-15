import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.memory.backfill import BackfillReport, EmbeddingBackfill
from app.domain.memory.types import (
    MemoryDurability,
    MemorySource,
    MemoryStatus,
    MemoryType,
)
from app.infrastructure.database.models import Memory, User
from app.infrastructure.llm.exceptions import (
    LLMConfigurationError,
    LLMError,
    LLMInvalidOutputError,
    LLMUnavailableError,
)
from app.retrieval.semantic import SemanticMemoryRetriever
from tests.fakes import FakeEmbeddingProvider
from tests.integration.conftest import sessions_like

pytestmark = pytest.mark.integration

MODEL = "test-embedding-model"
OLD_MODEL = "retired-embedding-model"


class ScriptedEmbeddings(FakeEmbeddingProvider):
    """Deterministic vectors, with every call recorded and failures on cue."""

    def __init__(
        self,
        *,
        rejects: frozenset[str] = frozenset(),
        outages: int = 0,
        failure: LLMError | None = None,
        during_call: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__()
        # Any call containing one of these texts is refused, as a provider
        # refuses a whole batch over one bad input.
        self._rejects = rejects
        # This many calls fail as unavailable before the provider recovers.
        self._outages = outages
        self._always = failure
        self._during_call = during_call
        self.calls: list[list[str]] = []

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self._during_call is not None:
            await self._during_call()
        if self._always is not None:
            raise self._always
        if self._outages:
            self._outages -= 1
            raise LLMUnavailableError("provider is down")
        if self._rejects.intersection(texts):
            raise LLMInvalidOutputError("provider rejected the request")
        return await super().embed_documents(texts)


@pytest.fixture
async def user(clean_database: None, db_session: AsyncSession) -> User:
    return await make_user(db_session)


async def make_user(session: AsyncSession) -> User:
    account = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@example.com", password_hash="x")
    session.add(account)
    await session.commit()
    return account


async def vector_for(text: str) -> list[float]:
    return await FakeEmbeddingProvider().embed_query(text)


async def store(
    session: AsyncSession,
    owner: User,
    content: str,
    *,
    model: str | None = None,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    expires_at: datetime | None = None,
    vector: bool | None = None,
) -> Memory:
    """A memory embedded with ``model``, or with no embedding when model is None.

    ``vector`` overrides whether a vector is stored, for rows whose vector and
    model name disagree.
    """
    has_vector = model is not None if vector is None else vector
    memory = Memory(
        id=uuid.uuid4(),
        user_id=owner.id,
        content=content,
        memory_type=MemoryType.FACT,
        status=status,
        durability=MemoryDurability.LONG_TERM,
        source=MemorySource.CONVERSATION,
        importance=0.6,
        confidence=0.9,
        expires_at=expires_at,
        embedding=await vector_for(content) if has_vector else None,
        embedding_model=model,
    )
    session.add(memory)
    await session.commit()
    return memory


async def reload(session: AsyncSession, memory: Memory) -> Memory:
    """The row as committed, read through a session of its own."""
    async with sessions_like(session)() as fresh:
        current = await fresh.get(Memory, memory.id)
    assert current is not None
    return current


def backfill(
    session: AsyncSession,
    embeddings: FakeEmbeddingProvider,
    *,
    batch_size: int = 50,
    include_inactive: bool = False,
) -> EmbeddingBackfill:
    return EmbeddingBackfill(
        sessions_like(session),
        embeddings,
        MODEL,
        batch_size=batch_size,
        include_inactive=include_inactive,
    )


async def run_to_completion(job: EmbeddingBackfill) -> BackfillReport:
    """Run, failing rather than hanging if a run ever revisits what it left stale."""
    async with asyncio.timeout(30):
        return await job.run()


async def found_by_meaning(session: AsyncSession, owner: User, text: str) -> bool:
    hits = await SemanticMemoryRetriever(sessions_like(session)).search(
        owner.id,
        await vector_for(text),
        embedding_model=MODEL,
        limit=10,
        min_similarity=0.9,
        now=datetime.now(tz=UTC),
    )
    return text in [hit.content for hit in hits]


class TestWhatGetsEmbedded:
    async def test_memories_search_could_not_use_become_findable_by_meaning(
        self, db_session: AsyncSession, user: User
    ):
        failed = await store(db_session, user, "I keep bees on the allotment")
        outdated = await store(db_session, user, "I race dinghies on Sundays", model=OLD_MODEL)
        unnamed = await store(
            db_session, user, "I collect vintage fountain pens", model=None, vector=True
        )
        stale = (failed, outdated, unnamed)

        for memory in stale:
            assert not await found_by_meaning(db_session, user, memory.content)

        report = await backfill(db_session, ScriptedEmbeddings()).run()

        assert report.embedded == 3
        for memory in stale:
            assert await found_by_meaning(db_session, user, memory.content)
            assert (await reload(db_session, memory)).embedding_model == MODEL

    async def test_memories_already_embedded_with_the_current_model_are_not_sent(
        self, db_session: AsyncSession, user: User
    ):
        current = await store(db_session, user, "I speak Portuguese", model=MODEL)
        stale = await store(db_session, user, "I am learning the oboe")
        embeddings = ScriptedEmbeddings()

        await backfill(db_session, embeddings).run()

        assert embeddings.calls == [[stale.content]]
        assert (await reload(db_session, current)).embedding_model == MODEL

    async def test_every_users_memories_are_covered(self, db_session: AsyncSession, user: User):
        other = await make_user(db_session)
        mine = await store(db_session, user, "I run before work")
        theirs = await store(db_session, other, "I swim after work")

        await backfill(db_session, ScriptedEmbeddings()).run()

        assert await found_by_meaning(db_session, user, mine.content)
        assert await found_by_meaning(db_session, other, theirs.content)

    async def test_archived_superseded_and_expired_memories_are_skipped_by_default(
        self, db_session: AsyncSession, user: User
    ):
        """Search never returns them, so embedding them would be calls for nothing."""
        active = await store(db_session, user, "I cycle to the office")
        await store(db_session, user, "I used to cycle", status=MemoryStatus.ARCHIVED)
        await store(db_session, user, "I cycled in 2019", status=MemoryStatus.SUPERSEDED)
        await store(
            db_session,
            user,
            "I am cycling this week",
            expires_at=datetime.now(tz=UTC) - timedelta(days=1),
        )
        embeddings = ScriptedEmbeddings()

        report = await backfill(db_session, embeddings).run()

        assert embeddings.calls == [[active.content]]
        assert report.embedded == 1

    async def test_inactive_memories_can_be_included_but_expired_ones_never_are(
        self, db_session: AsyncSession, user: User
    ):
        """An archived or superseded memory can be restored; an expired one can't."""
        active = await store(db_session, user, "I cycle to the office")
        archived = await store(db_session, user, "I used to cycle", status=MemoryStatus.ARCHIVED)
        superseded = await store(
            db_session, user, "I cycled in 2019", status=MemoryStatus.SUPERSEDED
        )
        await store(
            db_session,
            user,
            "I am cycling this week",
            expires_at=datetime.now(tz=UTC) - timedelta(days=1),
        )
        embeddings = ScriptedEmbeddings()

        report = await backfill(db_session, embeddings, include_inactive=True).run()

        assert sorted(text for call in embeddings.calls for text in call) == sorted(
            [active.content, archived.content, superseded.content]
        )
        assert report.embedded == 3

    async def test_pending_counts_what_a_run_would_embed_without_calling_the_provider(
        self, db_session: AsyncSession, user: User
    ):
        await store(db_session, user, "I bake sourdough")
        await store(db_session, user, "I brew cider", model=OLD_MODEL)
        await store(db_session, user, "I grow chillies", model=MODEL)
        await store(db_session, user, "I knit", status=MemoryStatus.ARCHIVED)
        embeddings = ScriptedEmbeddings()

        assert await backfill(db_session, embeddings).pending() == 2
        assert await backfill(db_session, embeddings, include_inactive=True).pending() == 3
        assert embeddings.calls == []


class TestRunningIt:
    async def test_a_second_run_makes_no_embedding_calls(
        self, db_session: AsyncSession, user: User
    ):
        await store(db_session, user, "I play chess online")
        await store(db_session, user, "I follow Formula 1", model=OLD_MODEL)
        await store(db_session, user, "I am vegetarian", model=MODEL)

        first = await backfill(db_session, ScriptedEmbeddings()).run()
        assert first.embedded == 2

        embeddings = ScriptedEmbeddings()
        second = await backfill(db_session, embeddings).run()

        assert embeddings.calls == []
        assert second == BackfillReport()

    async def test_memories_are_embedded_in_batches_of_the_configured_size(
        self, db_session: AsyncSession, user: User
    ):
        for number in range(5):
            await store(db_session, user, f"Fact number {number} about me")
        embeddings = ScriptedEmbeddings()

        report = await backfill(db_session, embeddings, batch_size=2).run()

        assert [len(call) for call in embeddings.calls] == [2, 2, 1]
        assert report == BackfillReport(embedded=5, batches=3)

    async def test_embedding_does_not_change_when_the_memory_was_last_updated(
        self, db_session: AsyncSession, user: User
    ):
        """updated_at is shown to the user; a new vector is not an edit they made."""
        memory = await store(db_session, user, "I live in Lisbon")
        before = (await reload(db_session, memory)).updated_at

        await backfill(db_session, ScriptedEmbeddings()).run()

        after = await reload(db_session, memory)
        assert after.embedding_model == MODEL
        assert after.updated_at == before


class TestProviderFailures:
    async def test_a_memory_the_provider_rejects_is_left_as_it_was(
        self, db_session: AsyncSession, user: User
    ):
        good = await store(db_session, user, "I teach yoga")
        bad = await store(db_session, user, "I teach pilates", model=OLD_MODEL)
        also_good = await store(db_session, user, "I teach spin")
        old_vector = list((await reload(db_session, bad)).embedding or [])
        embeddings = ScriptedEmbeddings(rejects=frozenset({bad.content}))

        report = await run_to_completion(backfill(db_session, embeddings))

        assert report.embedded == 2
        assert report.failed == 1
        for memory in (good, also_good):
            assert (await reload(db_session, memory)).embedding_model == MODEL
        untouched = await reload(db_session, bad)
        assert untouched.embedding_model == OLD_MODEL
        assert list(untouched.embedding or []) == old_vector

    async def test_the_next_run_retries_only_what_failed(
        self, db_session: AsyncSession, user: User
    ):
        await store(db_session, user, "I teach yoga")
        bad = await store(db_session, user, "I teach pilates")
        await run_to_completion(
            backfill(db_session, ScriptedEmbeddings(rejects=frozenset({bad.content})))
        )

        embeddings = ScriptedEmbeddings()
        report = await backfill(db_session, embeddings).run()

        assert embeddings.calls == [[bad.content]]
        assert report.embedded == 1
        assert await found_by_meaning(db_session, user, bad.content)

    async def test_an_outage_skips_that_batch_and_the_run_carries_on(
        self, db_session: AsyncSession, user: User
    ):
        stored = [await store(db_session, user, f"Hobby number {n}") for n in range(4)]
        first_batch, second_batch = (
            sorted(stored, key=lambda m: m.id)[:2],
            sorted(stored, key=lambda m: m.id)[2:],
        )
        embeddings = ScriptedEmbeddings(outages=1)

        report = await run_to_completion(backfill(db_session, embeddings, batch_size=2))

        # One call per batch: an outage is not retried memory by memory.
        assert len(embeddings.calls) == 2
        assert report == BackfillReport(embedded=2, failed=2, batches=2)
        for memory in first_batch:
            assert (await reload(db_session, memory)).embedding is None
        for memory in second_batch:
            assert (await reload(db_session, memory)).embedding_model == MODEL

    async def test_rejected_credentials_stop_the_run_and_write_nothing(
        self, db_session: AsyncSession, user: User
    ):
        """Every remaining call would fail the same way."""
        memories = [await store(db_session, user, f"Detail number {n}") for n in range(3)]
        embeddings = ScriptedEmbeddings(failure=LLMConfigurationError("no key"))

        with pytest.raises(LLMConfigurationError):
            await backfill(db_session, embeddings, batch_size=1).run()

        assert len(embeddings.calls) == 1
        for memory in memories:
            assert (await reload(db_session, memory)).embedding is None


class TestConcurrentChanges:
    async def test_an_edit_made_while_embedding_is_not_overwritten(
        self, db_session: AsyncSession, user: User
    ):
        """The vector was computed from the old words, so it must not land."""
        memory = await store(db_session, user, "I work at a bakery")

        async def user_edits_it() -> None:
            async with sessions_like(db_session)() as session:
                await session.execute(
                    update(Memory)
                    .where(Memory.id == memory.id)
                    .values(content="I work at a library", embedding=None, embedding_model=None)
                )
                await session.commit()

        report = await backfill(db_session, ScriptedEmbeddings(during_call=user_edits_it)).run()

        assert report == BackfillReport(changed=1, batches=1)
        edited = await reload(db_session, memory)
        assert edited.content == "I work at a library"
        assert edited.embedding is None

    async def test_a_memory_embedded_elsewhere_meanwhile_is_left_alone(
        self, db_session: AsyncSession, user: User
    ):
        memory = await store(db_session, user, "I work at a bakery")
        elsewhere = await vector_for("embedded by another process")

        async def someone_else_embeds_it() -> None:
            async with sessions_like(db_session)() as session:
                await session.execute(
                    update(Memory)
                    .where(Memory.id == memory.id)
                    .values(embedding=elsewhere, embedding_model=MODEL)
                )
                await session.commit()

        report = await backfill(
            db_session, ScriptedEmbeddings(during_call=someone_else_embeds_it)
        ).run()

        assert report == BackfillReport(changed=1, batches=1)
        assert list((await reload(db_session, memory)).embedding or []) == pytest.approx(elsewhere)
