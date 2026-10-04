from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app

_SETTINGS_ENV_VARS = (
    "APP_NAME",
    "APP_VERSION",
    "ENVIRONMENT",
    "DEBUG",
    "LOG_LEVEL",
    "LOG_FORMAT",
    "DATABASE_URL",
    "JWT_SECRET",
    "REDIS_URL",
    "RATE_LIMIT_ENABLED",
    "USAGE_LIMITS_ENABLED",
)

# A valid URL is needed to build the engine; nothing connects.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused"


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests independent of the shell environment of whoever runs them."""
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        log_format="console",
        log_level="WARNING",
        database_url=UNREACHABLE_DATABASE_URL,
        jwt_secret="unit-test-signing-key-that-is-long-enough",
        # Limits off by default; limit tests turn them on.
        rate_limit_enabled=False,
        usage_limits_enabled=False,
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    yield application
    # The test transport does not run the lifespan, so the pool is closed here.
    await application.state.database_engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client
