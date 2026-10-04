import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.api import health
from app.core.config import Settings
from app.main import create_app


async def test_health_returns_data_envelope(client: AsyncClient, settings: Settings):
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == {
        "status": "ok",
        "app_name": settings.app_name,
        "version": settings.app_version,
        "environment": "test",
    }


async def test_ready_reports_not_ready_when_database_is_unreachable(client: AsyncClient):
    response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["data"] == {
        "status": "not_ready",
        "checks": {"database": "failed"},
    }


async def test_ready_reports_ready_when_every_check_passes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    async def _reachable(*_: object, **__: object) -> bool:
        return True

    monkeypatch.setattr(health, "check_database_connection", _reachable)

    response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ready", "checks": {"database": "ok"}}


async def test_health_does_not_depend_on_the_database(client: AsyncClient):
    """Liveness must stay green while dependencies are down, or orchestrators
    will restart a process that is working correctly."""
    assert (await client.get("/health")).status_code == 200


async def test_response_carries_generated_correlation_id(client: AsyncClient):
    response = await client.get("/health")

    request_id = response.headers["x-request-id"]
    assert len(request_id) == 32
    assert response.json()["meta"]["request_id"] == request_id


async def test_inbound_correlation_id_is_propagated(client: AsyncClient):
    response = await client.get("/health", headers={"x-request-id": "client-supplied-1"})

    assert response.headers["x-request-id"] == "client-supplied-1"
    assert response.json()["meta"]["request_id"] == "client-supplied-1"


async def test_unsafe_inbound_correlation_id_is_not_reflected(client: AsyncClient):
    response = await client.get("/health", headers={"x-request-id": "not a valid id"})

    assert response.headers["x-request-id"] != "not a valid id"


async def test_docs_are_served_outside_production(client: AsyncClient):
    assert (await client.get("/openapi.json")).status_code == 200


async def test_docs_are_disabled_in_production():
    production_app: FastAPI = create_app(
        Settings(
            environment="production",
            database_url="postgresql+asyncpg://user:secret@localhost:5433/pia",
            jwt_secret="production-signing-key-that-is-long-enough",
            openai_api_key="sk-not-a-real-key",
        )
    )

    assert production_app.docs_url is None
    assert production_app.openapi_url is None
