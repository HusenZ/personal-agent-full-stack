import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.core.exceptions import ApplicationError, NotFoundError, PermissionDeniedError


class _Payload(BaseModel):
    email: str
    password: str


@pytest.fixture
def app_with_failing_routes(app: FastAPI) -> FastAPI:
    @app.get("/_test/not-found")
    async def _not_found() -> None:
        raise NotFoundError("Memory was not found.")

    @app.get("/_test/forbidden")
    async def _forbidden() -> None:
        raise PermissionDeniedError()

    @app.get("/_test/application-error")
    async def _application_error() -> None:
        raise ApplicationError()

    @app.get("/_test/crash")
    async def _crash() -> None:
        raise RuntimeError("database socket closed unexpectedly")

    @app.post("/_test/validated")
    async def _validated(payload: _Payload) -> dict[str, str]:
        return {"email": payload.email}

    return app


@pytest.fixture
async def failing_client(app_with_failing_routes: FastAPI):
    transport = ASGITransport(app=app_with_failing_routes, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


async def test_domain_error_maps_to_status_and_error_envelope(failing_client: AsyncClient):
    response = await failing_client.get("/_test/not-found")

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "NOT_FOUND",
        "message": "Memory was not found.",
    }


async def test_permission_denied_maps_to_forbidden(failing_client: AsyncClient):
    response = await failing_client.get("/_test/forbidden")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


async def test_unmapped_application_error_is_server_error(failing_client: AsyncClient):
    response = await failing_client.get("/_test/application-error")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


async def test_unexpected_exception_does_not_leak_internals(failing_client: AsyncClient):
    response = await failing_client.get("/_test/crash")

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == {
        "code": "INTERNAL_ERROR",
        "message": "An unexpected error occurred.",
    }
    assert "database socket" not in response.text
    assert "Traceback" not in response.text


async def test_unexpected_exception_response_is_correlated(failing_client: AsyncClient):
    response = await failing_client.get("/_test/crash")

    assert response.headers["x-request-id"]
    assert response.json()["meta"]["request_id"] == response.headers["x-request-id"]


async def test_unknown_route_uses_the_error_envelope(failing_client: AsyncClient):
    response = await failing_client.get("/_test/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_validation_error_reports_fields_without_echoing_values(
    failing_client: AsyncClient,
):
    response = await failing_client.post("/_test/validated", json={"email": "a@b.c"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [detail["field"] for detail in error["details"]] == ["body.password"]


async def test_validation_error_does_not_echo_submitted_values(failing_client: AsyncClient):
    response = await failing_client.post(
        "/_test/validated", json={"email": 1, "password": "hunter2"}
    )

    assert response.status_code == 422
    assert "hunter2" not in response.text


async def test_an_error_response_carries_its_request_id_once(api_client):
    """Sent twice, clients read one comma-joined value that matches no log line."""
    response = await api_client.get("/api/v1/profile")

    assert response.status_code == 401
    assert len(response.headers.get_list("x-request-id")) == 1
