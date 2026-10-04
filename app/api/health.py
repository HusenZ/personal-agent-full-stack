from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel
from starlette import status

from app.api.dependencies import DatabaseEngineDep, RedisClientDep, SettingsDep
from app.core.context import get_request_id
from app.infrastructure.cache.redis import check_redis_connection
from app.infrastructure.database.health import check_database_connection
from app.schemas.common import DataResponse, ResponseMeta

router = APIRouter(tags=["infrastructure"])


class LivenessStatus(BaseModel):
    status: Literal["ok"]
    app_name: str
    version: str
    environment: str


class ReadinessStatus(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, Literal["ok", "failed"]]


def _meta() -> ResponseMeta:
    return ResponseMeta(request_id=get_request_id())


@router.get("/health", response_model=DataResponse[LivenessStatus], summary="Liveness probe")
async def health(settings: SettingsDep) -> DataResponse[LivenessStatus]:
    """Reports that the process is running. Deliberately checks no dependencies."""
    return DataResponse(
        data=LivenessStatus(
            status="ok",
            app_name=settings.app_name,
            version=settings.app_version,
            environment=settings.environment,
        ),
        meta=_meta(),
    )


@router.get("/ready", response_model=DataResponse[ReadinessStatus], summary="Readiness probe")
async def ready(
    response: Response,
    engine: DatabaseEngineDep,
    redis_client: RedisClientDep,
    settings: SettingsDep,
) -> DataResponse[ReadinessStatus]:
    """Reports whether the service can accept traffic."""
    database_reachable = await check_database_connection(
        engine, settings.database_health_timeout_seconds
    )
    checks: dict[str, Literal["ok", "failed"]] = {
        "database": "ok" if database_reachable else "failed",
    }

    # Redis only matters for readiness when rate limiting is enabled.
    if settings.rate_limit_enabled and settings.redis_url is not None:
        redis_reachable = await check_redis_connection(
            redis_client, settings.rate_limit_timeout_seconds
        )
        checks["redis"] = "ok" if redis_reachable else "failed"
    is_ready = all(result == "ok" for result in checks.values())

    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return DataResponse(
        data=ReadinessStatus(status="ready" if is_ready else "not_ready", checks=checks),
        meta=_meta(),
    )
