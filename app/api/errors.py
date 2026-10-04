import logging
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ExceptionHandler

from app.api.middleware import REQUEST_ID_HEADER
from app.application.subscription.exceptions import QuotaExceededError
from app.core.context import get_request_id
from app.core.exceptions import (
    ApplicationError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    ServiceUnavailableError,
)
from app.schemas.common import ErrorDetail, ErrorResponse, ResponseMeta

logger = logging.getLogger(__name__)

_STATUS_BY_ERROR: dict[type[ApplicationError], int] = {
    AuthenticationError: status.HTTP_401_UNAUTHORIZED,
    NotFoundError: status.HTTP_404_NOT_FOUND,
    PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    QuotaExceededError: status.HTTP_402_PAYMENT_REQUIRED,
    ConflictError: status.HTTP_409_CONFLICT,
    RateLimitedError: status.HTTP_429_TOO_MANY_REQUESTS,
    ServiceUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    ApplicationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
}

_CODE_BY_HTTP_STATUS: dict[int, str] = {
    status.HTTP_401_UNAUTHORIZED: "UNAUTHENTICATED",
    status.HTTP_403_FORBIDDEN: "PERMISSION_DENIED",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
    status.HTTP_409_CONFLICT: "CONFLICT",
    status.HTTP_413_CONTENT_TOO_LARGE: "PAYLOAD_TOO_LARGE",
    status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
    status.HTTP_503_SERVICE_UNAVAILABLE: "SERVICE_UNAVAILABLE",
}


def resolve_status_code(error: ApplicationError) -> int:
    # Walking the MRO lets subclasses inherit their parent's status mapping.
    for error_type in type(error).__mro__:
        if error_type in _STATUS_BY_ERROR:
            return _STATUS_BY_ERROR[error_type]
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def _error_response(
    status_code: int, detail: ErrorDetail, extra_headers: dict[str, str] | None = None
) -> JSONResponse:
    request_id = get_request_id()
    body = ErrorResponse(error=detail, meta=ResponseMeta(request_id=request_id))

    headers: dict[str, str] = {}
    if request_id:
        headers[REQUEST_ID_HEADER] = request_id
    if extra_headers:
        headers.update(extra_headers)
    if status_code == status.HTTP_401_UNAUTHORIZED:
        # RFC 6750 requires a Bearer challenge on 401.
        headers["WWW-Authenticate"] = "Bearer"

    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json", exclude_none=True),
        headers=headers or None,
    )


def _safe_validation_details(errors: list[Any]) -> list[dict[str, Any]]:
    # Never echo input values back; they may contain credentials or personal data.
    return [
        {
            "field": ".".join(str(part) for part in error.get("loc", ())),
            "message": error.get("msg", ""),
            "type": error.get("type", ""),
        }
        for error in errors
    ]


async def handle_application_error(_: Request, exc: ApplicationError) -> JSONResponse:
    status_code = resolve_status_code(exc)
    if status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        logger.exception("application_error", extra={"error_code": exc.code})

    headers = (
        {"Retry-After": str(exc.retry_after_seconds)} if isinstance(exc, RateLimitedError) else None
    )

    return _error_response(
        status_code,
        ErrorDetail(code=exc.code, message=exc.message, details=_error_details(exc)),
        extra_headers=headers,
    )


def _error_details(exc: ApplicationError) -> list[dict[str, Any]] | None:
    # Lets the client show what ran out, when it resets, and offer an upgrade.
    if isinstance(exc, QuotaExceededError):
        return [
            {
                "plan": exc.plan.value,
                "feature": exc.feature.value,
                "limit": exc.limit,
                "resets_at": exc.resets_at.isoformat(),
            }
        ]
    return None


async def handle_request_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return _error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        ErrorDetail(
            code="VALIDATION_ERROR",
            message="Request validation failed.",
            details=_safe_validation_details(list(exc.errors())),
        ),
    )


async def handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _CODE_BY_HTTP_STATUS.get(exc.status_code, "HTTP_ERROR")
    message = exc.detail if isinstance(exc.detail, str) else "Request could not be completed."
    return _error_response(exc.status_code, ErrorDetail(code=code, message=message))


async def handle_unexpected_error(_: Request, __: Exception) -> JSONResponse:
    # Runs outside the middleware, so the correlation id comes from the context.
    return _error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        ErrorDetail(code="INTERNAL_ERROR", message="An unexpected error occurred."),
    )


def register_exception_handlers(app: FastAPI) -> None:
    # Casts keep each handler's precise signature while satisfying Starlette.
    app.add_exception_handler(ApplicationError, cast(ExceptionHandler, handle_application_error))
    app.add_exception_handler(
        RequestValidationError, cast(ExceptionHandler, handle_request_validation_error)
    )
    app.add_exception_handler(StarletteHTTPException, cast(ExceptionHandler, handle_http_exception))
    app.add_exception_handler(Exception, handle_unexpected_error)
