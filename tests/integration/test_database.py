import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings
from app.infrastructure.database.health import check_database_connection
from app.infrastructure.database.session import create_session_factory
from app.main import create_app

pytestmark = pytest.mark.integration


async def test_health_check_passes_against_a_live_database(database_engine: AsyncEngine):
    assert await check_database_connection(database_engine, timeout_seconds=5.0) is True


async def test_health_check_reports_failure_for_an_unreachable_database():
    engine = create_async_engine(
        "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        connect_args={"timeout": 1.0},
    )
    try:
        assert await check_database_connection(engine, timeout_seconds=2.0) is False
    finally:
        await engine.dispose()


async def test_pgvector_extension_is_available(database_engine: AsyncEngine):
    """The retrieval phases depend on pgvector; migration 0001 installs it."""
    async with database_engine.connect() as connection:
        result = await connection.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
        assert result.scalar() == 1, "migration 0001 has not been applied to the test database"


async def test_session_factory_yields_a_usable_session(database_engine: AsyncEngine):
    session_factory = create_session_factory(database_engine)

    async with session_factory() as session:
        result = await session.execute(text("SELECT 42"))

        assert result.scalar() == 42


async def test_session_rolls_back_uncommitted_work(database_engine: AsyncEngine):
    """The request-scoped session must not persist anything a service did not commit."""
    session_factory = create_session_factory(database_engine)

    async with session_factory() as session:
        await session.execute(text("CREATE TEMP TABLE rollback_probe (id int)"))
        await session.execute(text("INSERT INTO rollback_probe VALUES (1)"))

    async with session_factory() as session:
        result = await session.execute(text("SELECT to_regclass('pg_temp.rollback_probe')"))

        assert result.scalar() is None


async def test_readiness_reports_ok_against_a_live_database(
    database_settings: Settings, database_engine: AsyncEngine
):
    app = create_app(database_settings)
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/ready")
    finally:
        await app.state.database_engine.dispose()

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ready", "checks": {"database": "ok"}}
