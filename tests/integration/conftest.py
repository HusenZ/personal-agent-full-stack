import os
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.infrastructure.database.session import create_session_factory
from app.main import create_app
from app.workers.runner import JobWorker, build_worker
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider

# Defaults to the docker-compose database.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://pia:pia@localhost:5433/pia"
)

# Separate Redis DB so flushing it can't touch local dev data.
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6380/1")

# Truncated between tests; CASCADE makes FK order irrelevant.
_MANAGED_TABLES = (
    "users",
    "refresh_tokens",
    "profiles",
    "goals",
    "constraints",
    "preferences",
    "onboarding_sessions",
    "analyses",
    "conversations",
    "messages",
    "memories",
    "memory_versions",
    "events",
    "agent_runs",
    "background_jobs",
    "week_plans",
    "subscriptions",
    "usage_counters",
)


@pytest.fixture
async def database_engine() -> AsyncIterator[AsyncEngine]:
    """Engine bound to the test database, skipping the test when none is running.

    NullPool keeps each test's connections from outliving it, which matters when
    a test drops or recreates objects other tests rely on.
    """
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError):
        await engine.dispose()
        pytest.skip(f"no test database reachable at {TEST_DATABASE_URL}")

    yield engine

    await engine.dispose()


@pytest.fixture
async def clean_database(database_engine: AsyncEngine) -> AsyncIterator[None]:
    async with database_engine.begin() as connection:
        missing = await connection.execute(text("SELECT to_regclass('public.users')"))
        if missing.scalar() is None:
            pytest.fail(
                "test database has no schema; run 'uv run alembic upgrade head' against "
                f"{TEST_DATABASE_URL}"
            )
        await connection.execute(
            text(f"TRUNCATE {', '.join(_MANAGED_TABLES)} RESTART IDENTITY CASCADE")
        )

    yield


@pytest.fixture
def database_settings() -> Settings:
    return Settings(
        environment="test",
        log_format="console",
        log_level="WARNING",
        database_url=TEST_DATABASE_URL,
        jwt_secret="test-signing-key-that-is-long-enough-to-pass",
        access_token_ttl_seconds=900,
        # Limits off by default; limit tests turn them on.
        rate_limit_enabled=False,
        usage_limits_enabled=False,
        embedding_model="test-embedding-model",
    )


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    """Default double. Override in a test module to script model behaviour."""
    return FakeLLMProvider()


@pytest.fixture
def embedding_provider() -> FakeEmbeddingProvider:
    """Deterministic vectors, so retrieval tests assert on meaning not chance."""
    return FakeEmbeddingProvider()


@pytest.fixture
async def api_app(
    database_settings: Settings,
    clean_database: None,
    llm_provider: FakeLLMProvider,
    embedding_provider: FakeEmbeddingProvider,
) -> AsyncIterator[FastAPI]:
    # Fake providers keep the suite off the network.
    application = create_app(
        database_settings,
        llm_provider=llm_provider,
        embedding_provider=embedding_provider,
    )

    yield application
    await application.state.database_engine.dispose()


def app_worker(application: FastAPI) -> JobWorker:
    """The background worker, wired to the same app state as the API."""
    return build_worker(
        application.state.settings,
        application.state.database_session_factory,
        application.state.memory_graph,
        application.state.embedding_provider,
    )


async def drain_jobs(application: FastAPI) -> int:
    """Run every due job through the real worker path. Returns how many ran."""
    worker = app_worker(application)
    processed = 0
    while await worker.run_once():
        processed += 1
    return processed


@pytest.fixture
async def api_client(api_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A client after whose every response the job queue is drained.

    Stands in for a worker that picks jobs up promptly, so tests about what the
    system learned from a conversation read the same as they would against a
    deployment. Tests about the queue itself use the worker directly instead.
    """

    async def drain(_: Response) -> None:
        await drain_jobs(api_app)

    transport = ASGITransport(app=api_app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", event_hooks={"response": [drain]}
    ) as client:
        yield client


@pytest.fixture
async def plain_client(api_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A client with no worker behind it: queued jobs stay queued."""
    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
async def db_session(database_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Direct database access for arranging state the API cannot reach."""
    async with create_session_factory(database_engine)() as session:
        yield session


def sessions_like(session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    """A session factory on the same engine as a test's session.

    Retrievers open a short session per search; this lets a test that arranged
    data through its own session hand them the matching factory.
    """
    assert isinstance(session.bind, AsyncEngine)
    return create_session_factory(session.bind)
