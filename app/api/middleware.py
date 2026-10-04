import logging
import re
import time
import uuid
from collections.abc import Iterable

from starlette import status
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import bind_request_id

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "x-request-id"

# Inbound ids end up in logs, so only a safe token shape is accepted.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _resolve_request_id(scope: Scope) -> str:
    headers: Iterable[tuple[bytes, bytes]] = scope.get("headers", ())
    for raw_name, raw_value in headers:
        if raw_name == REQUEST_ID_HEADER.encode():
            candidate: str = raw_value.decode("latin-1")
            if _SAFE_REQUEST_ID.match(candidate):
                return candidate
            break
    return uuid.uuid4().hex


class RequestContextMiddleware:
    """Assigns a correlation id to every request and emits one access log record.

    Implemented as raw ASGI middleware rather than ``BaseHTTPMiddleware`` so the
    correlation id is set in the same context the outer error handlers run in,
    which keeps it available on responses produced by unhandled exceptions.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _resolve_request_id(scope)
        bind_request_id(request_id)

        status_code = 500
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                # Error responses already set it; setting it twice breaks clients.
                if not any(name.lower() == REQUEST_ID_HEADER.encode() for name, _ in headers):
                    headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            # Error handlers build the response; this just logs it with the request id.
            logger.exception(
                "http_request_failed",
                extra=self._log_fields(scope, status_code, started),
            )
            raise

        logger.info("http_request", extra=self._log_fields(scope, status_code, started))

    @staticmethod
    def _log_fields(scope: Scope, status_code: int, started: float) -> dict[str, object]:
        # Query string omitted: it may carry personal data.
        return {
            "http_method": scope.get("method"),
            "http_path": scope.get("path"),
            "http_status": status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }


class BodySizeLimitMiddleware:
    """Refuses request bodies larger than any endpoint accepts, before reading them.

    Every schema bounds its fields, but only after the whole body has been read
    and parsed: a 50 MB login attempt allocated over 150 MB before validation
    rejected it, without authentication. Enforced as the body is received, so a
    declared oversize fails on the first read and an undeclared one as soon as
    it crosses the limit. Raised inside the application as an HTTP error: FastAPI
    turns any other exception during body parsing into a generic 400, and this
    way it is answered by the usual handlers, in the usual envelope, as a 413.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _content_length(scope)
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            if declared is not None and declared > self.max_bytes:
                raise _too_large()
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _too_large()
            return message

        await self.app(scope, limited_receive, send)


class SecurityHeadersMiddleware:
    """Headers every API response should carry.

    nosniff stops a browser reinterpreting a JSON body as something executable.
    no-store, unless a response sets its own caching, keeps tokens and personal
    data out of shared and browser caches.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.append((b"x-content-type-options", b"nosniff"))
                if b"cache-control" not in present:
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _too_large() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="The request body is too large."
    )
