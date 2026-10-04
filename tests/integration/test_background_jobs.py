"""The background job queue against real PostgreSQL.

Most tests use the client without a worker behind it, so queued jobs stay
queued until a test runs the worker itself.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.domain.memory.types import (
    MemoryCandidate,
    MemoryDurability,
    MemoryExtraction,
    MemoryType,
)
from app.infrastructure.database.models import (
    BackgroundJob,
    Event,
    JobKind,
    JobStatus,
    Memory,
    User,
)
from app.infrastructure.database.repositories.job import JobRepository
from app.main import create_app
from app.retrieval.hybrid import HybridMemoryRetriever
from app.workers.runner import JobWorker
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider
from tests.integration.conftest import app_worker
from tests.integration.test_chat import authenticate

pytestmark = pytest.mark.integration

QUESTION = "I ran 5 kilometres this morning before work."
EVENT = MemoryCandidate(
    content="I ran 5 kilometres this morning",
    memory_type=MemoryType.EVENT,
    durability=MemoryDurability.LONG_TERM,
    importance=0.6,
    confidence=0.9,
    activity="running",
    quantity=5,
    unit="kilometres",
    days_ago=0,
)


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(
        replies=["Nice run."], extractions_of_memory=[MemoryExtraction(candidates=[EVENT])]
    )


async def chat(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.post("/api/v1/chat", headers=headers, json={"message": QUESTION})
    assert response.status_code == 201
    data: dict[str, Any] = response.json()["data"]
    return data


async def jobs(session: AsyncSession) -> list[BackgroundJob]:
    session.expire_all()
    return list((await session.scalars(select(BackgroundJob))).all())


async def count(session: AsyncSession, model: Any) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


class TestEnqueueing:
    async def test_an_exchange_queues_one_extraction_job(
        self, plain_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(plain_client, "ada@example.com")
        reply = await chat(plain_client, headers)

        (job,) = await jobs(db_session)
        assert job.kind is JobKind.MEMORY_EXTRACTION
        assert job.status is JobStatus.PENDING
        assert job.payload["conversation_id"] == reply["conversation_id"]
        assert job.payload["reply_message_id"] == reply["reply"]["id"]
        # Nothing has been learned yet: the worker has not run.
        assert await count(db_session, Memory) == 0

    async def test_the_queue_holds_references_not_content(
        self, plain_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(plain_client, "ada@example.com")
        await chat(plain_client, headers)

        (job,) = await jobs(db_session)
        assert "kilometres" not in str(job.payload)
        assert "Nice run" not in str(job.payload)

    async def test_a_failed_turn_queues_nothing(
        self,
        plain_client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The job shares the exchange's transaction, so it rolls back with it."""
        headers = await authenticate(plain_client, "ada@example.com")

        async def lost_connection(self: Any, *args: Any, **kwargs: Any) -> list[Any]:
            raise ConnectionError("database went away")

        monkeypatch.setattr(HybridMemoryRetriever, "retrieve", lost_connection)
        await plain_client.post("/api/v1/chat/stream", headers=headers, json={"message": QUESTION})

        assert await jobs(db_session) == []

    async def test_extraction_switched_off_queues_nothing(
        self,
        database_settings: Settings,
        clean_database: None,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
    ):
        from httpx import ASGITransport

        app = create_app(
            database_settings.model_copy(update={"memory_extraction_enabled": False}),
            llm_provider=llm_provider,
            embedding_provider=FakeEmbeddingProvider(),
        )
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                await chat(client, await authenticate(client, "ada@example.com"))
        finally:
            await app.state.database_engine.dispose()

        assert await jobs(db_session) == []


class TestProcessing:
    async def test_the_worker_learns_from_the_exchange_and_finishes_the_job(
        self, plain_client: AsyncClient, api_app: FastAPI, db_session: AsyncSession
    ):
        headers = await authenticate(plain_client, "ada@example.com")
        await chat(plain_client, headers)

        assert await app_worker(api_app).run_once() is True

        (job,) = await jobs(db_session)
        assert job.status is JobStatus.SUCCEEDED
        assert job.attempts == 1
        assert job.finished_at is not None
        assert await count(db_session, Memory) == 1
        assert await count(db_session, Event) == 1

    async def test_a_finished_job_is_never_run_again(
        self, plain_client: AsyncClient, api_app: FastAPI, db_session: AsyncSession
    ):
        """Running it twice would record the same run twice and double a total."""
        headers = await authenticate(plain_client, "ada@example.com")
        await chat(plain_client, headers)
        worker = app_worker(api_app)

        assert await worker.run_once() is True
        assert await worker.run_once() is False
        assert await count(db_session, Event) == 1

    async def test_an_exchange_deleted_before_the_worker_ran_finishes_quietly(
        self, plain_client: AsyncClient, api_app: FastAPI, db_session: AsyncSession
    ):
        headers = await authenticate(plain_client, "ada@example.com")
        await chat(plain_client, headers)
        await db_session.execute(text("DELETE FROM messages"))
        await db_session.commit()

        await app_worker(api_app).run_once()

        (job,) = await jobs(db_session)
        assert job.status is JobStatus.SUCCEEDED
        assert await count(db_session, Memory) == 0

    async def test_a_job_never_reads_another_users_messages(
        self, plain_client: AsyncClient, api_app: FastAPI, db_session: AsyncSession
    ):
        victim = await authenticate(plain_client, "grace@example.com")
        await chat(plain_client, victim)
        await authenticate(plain_client, "ada@example.com")
        attacker_id = await db_session.scalar(
            select(User.id).where(User.email == "ada@example.com")
        )

        # A job owned by one user, pointing at another user's exchange.
        await db_session.execute(update(BackgroundJob).values(user_id=attacker_id))
        await db_session.commit()

        await app_worker(api_app).run_once()

        assert await count(db_session, Memory) == 0


def worker_with(api_app: FastAPI, handler: Any) -> JobWorker:
    base = app_worker(api_app)
    return JobWorker(
        session_factory=base.session_factory,
        handlers={JobKind.MEMORY_EXTRACTION: handler},
        poll_interval=0.01,
        timeout=5,
        lease=timedelta(minutes=5),
        retry_base=timedelta(seconds=30),
    )


async def failing_handler(session: AsyncSession, job: BackgroundJob, *, now: datetime) -> bool:
    raise TimeoutError("provider took too long")


class TestFailure:
    async def test_a_failed_job_is_retried_later_not_immediately(
        self, plain_client: AsyncClient, api_app: FastAPI, db_session: AsyncSession
    ):
        await chat(plain_client, await authenticate(plain_client, "ada@example.com"))
        worker = worker_with(api_app, failing_handler)

        assert await worker.run_once() is True
        (job,) = await jobs(db_session)
        assert job.status is JobStatus.PENDING
        assert job.attempts == 1
        assert job.last_error_type == "TimeoutError"
        assert job.run_after > datetime.now(tz=UTC) + timedelta(seconds=20)

        # Not due yet, so nothing is claimed.
        assert await worker.run_once() is False

    async def test_a_job_that_keeps_failing_stops_at_its_attempt_limit(
        self, plain_client: AsyncClient, api_app: FastAPI, db_session: AsyncSession
    ):
        await chat(plain_client, await authenticate(plain_client, "ada@example.com"))
        worker = worker_with(api_app, failing_handler)

        for _ in range(10):
            await db_session.execute(
                update(BackgroundJob)
                .where(BackgroundJob.status == JobStatus.PENDING)
                .values(run_after=datetime.now(tz=UTC) - timedelta(seconds=1))
            )
            await db_session.commit()
            if not await worker.run_once():
                break

        (job,) = await jobs(db_session)
        assert job.status is JobStatus.FAILED
        assert job.attempts == job.max_attempts == 3
        assert job.finished_at is not None

    async def test_work_that_fails_after_marking_success_is_rolled_back(
        self, plain_client: AsyncClient, api_app: FastAPI, db_session: AsyncSession
    ):
        """Completion commits with the work or not at all."""
        await chat(plain_client, await authenticate(plain_client, "ada@example.com"))

        async def half_done(session: AsyncSession, job: BackgroundJob, *, now: datetime) -> bool:
            assert job.claim_token is not None
            await JobRepository(session).mark_succeeded(job.id, job.claim_token, now)
            raise RuntimeError("failed part way through")

        await worker_with(api_app, half_done).run_once()

        (job,) = await jobs(db_session)
        assert job.status is JobStatus.PENDING
        assert job.last_error_type == "RuntimeError"


class TestClaims:
    async def test_two_workers_never_take_the_same_job(
        self, plain_client: AsyncClient, api_app: FastAPI, database_engine: AsyncEngine
    ):
        await chat(plain_client, await authenticate(plain_client, "ada@example.com"))
        factory = api_app.state.database_session_factory
        now = datetime.now(tz=UTC)

        async with factory() as first, factory() as second:
            taken = await JobRepository(first).claim(now=now, lease=timedelta(minutes=5))
            # The first claim's transaction is still open and holds the row.
            also_taken = await JobRepository(second).claim(now=now, lease=timedelta(minutes=5))
            await first.commit()
            await second.commit()

        assert taken is not None
        assert also_taken is None

    async def test_a_job_whose_worker_died_is_claimed_again_after_its_lease(
        self, plain_client: AsyncClient, api_app: FastAPI, db_session: AsyncSession
    ):
        await chat(plain_client, await authenticate(plain_client, "ada@example.com"))
        factory = api_app.state.database_session_factory
        async with factory() as session:
            await JobRepository(session).claim(now=datetime.now(tz=UTC), lease=timedelta(minutes=5))
            await session.commit()

        # Claimed and abandoned: not claimable while the lease stands...
        assert await app_worker(api_app).run_once() is False
        await db_session.execute(
            update(BackgroundJob).values(locked_until=datetime.now(tz=UTC) - timedelta(seconds=1))
        )
        await db_session.commit()

        # ...and finished by another worker once it has run out.
        assert await app_worker(api_app).run_once() is True
        (job,) = await jobs(db_session)
        assert job.status is JobStatus.SUCCEEDED
        assert job.attempts == 2

    async def test_a_superseded_claim_commits_nothing(
        self, plain_client: AsyncClient, api_app: FastAPI, db_session: AsyncSession
    ):
        await chat(plain_client, await authenticate(plain_client, "ada@example.com"))
        factory = api_app.state.database_session_factory
        now = datetime.now(tz=UTC)

        async with factory() as session:
            stale = await JobRepository(session).claim(now=now, lease=timedelta(seconds=1))
            await session.commit()
        async with factory() as session:
            current = await JobRepository(session).claim(
                now=now + timedelta(seconds=5), lease=timedelta(minutes=5)
            )
            await session.commit()
        assert stale is not None and current is not None

        handler = app_worker(api_app).handlers[JobKind.MEMORY_EXTRACTION]
        async with factory() as session:
            assert await handler(session, stale, now=now) is False

        assert await count(db_session, Memory) == 0
        (job,) = await jobs(db_session)
        assert job.status is JobStatus.RUNNING

    async def test_the_claim_query_can_use_its_partial_index(
        self, db_session: AsyncSession, clean_database: None
    ):
        """The index predicate must match how statuses are actually stored."""
        await db_session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = (
            (
                await db_session.execute(
                    text(
                        "EXPLAIN SELECT id FROM background_jobs "
                        "WHERE status IN ('PENDING', 'RUNNING') AND run_after <= now() "
                        "ORDER BY run_after LIMIT 1"
                    )
                )
            )
            .scalars()
            .all()
        )

        assert "ix_background_jobs_claimable" in "\n".join(plan)


class TestRunLoop:
    async def test_a_stop_signal_ends_the_loop_promptly(self, api_app: FastAPI):
        stop = asyncio.Event()
        task = asyncio.create_task(worker_with(api_app, failing_handler).run(stop))
        await asyncio.sleep(0.05)

        stop.set()

        await asyncio.wait_for(task, timeout=1)

    async def test_the_loop_survives_a_database_outage(
        self, api_app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ):
        calls = 0

        async def unavailable(self: JobRepository, **kwargs: Any) -> None:
            nonlocal calls
            calls += 1
            raise ConnectionError("database is restarting")

        monkeypatch.setattr(JobRepository, "claim", unavailable)
        stop = asyncio.Event()
        task = asyncio.create_task(worker_with(api_app, failing_handler).run(stop))
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.wait_for(task, timeout=1)

        assert calls > 1


class TestBackoff:
    def test_each_retry_waits_twice_as_long_up_to_a_ceiling(self, api_app: FastAPI):
        worker = worker_with(api_app, failing_handler)

        assert [worker.backoff(n).total_seconds() for n in (1, 2, 3)] == [30, 60, 120]
        assert worker.backoff(20) == timedelta(hours=1)
