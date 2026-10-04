import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.config import LogFormat, LogLevel
from app.core.context import get_request_id
from app.infrastructure.observability.tracing import current_trace

# Drop uvicorn's ANSI-coloured duplicate of the message.
_RESERVED_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
    "color_message",
}


def reserved_log_fields() -> frozenset[str]:
    """Names that cannot be used as structured log fields.

    ``logging`` raises when an ``extra`` key shadows an attribute LogRecord sets
    itself, and several of them are ordinary domain words: "created", "message",
    "name", "module", "args". A test asserts no log site in the application uses
    one, because the failure appears at runtime and only on the path that logs.
    """
    return frozenset(_RESERVED_RECORD_ATTRS)


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: value for key, value in record.__dict__.items() if key not in _RESERVED_RECORD_ATTRS
    }


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }

        request_id = get_request_id()
        if request_id is not None:
            payload["request_id"] = request_id

        # Read from the trace so logs and traces always agree on the run.
        trace = current_trace()
        if trace is not None:
            payload["run_id"] = str(trace.run_id)

        payload.update(_extra_fields(record))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable output for local development only."""

    def format(self, record: logging.LogRecord) -> str:
        request_id = get_request_id()
        trace = current_trace()
        ids = [value for value in (request_id, trace and f"run:{trace.run_id}") if value]
        prefix = f"[{' '.join(ids)}] " if ids else ""
        extras = _extra_fields(record)
        suffix = " " + " ".join(f"{k}={v}" for k, v in extras.items()) if extras else ""
        base = f"{record.levelname:<8} {prefix}{record.name} {record.getMessage()}{suffix}"
        if record.exc_info:
            return f"{base}\n{self.formatException(record.exc_info)}"
        return base


def configure_logging(level: LogLevel, log_format: LogFormat) -> None:
    """Install a single stderr handler on the root logger.

    Existing handlers are replaced so that repeated application construction
    (notably in tests) does not emit duplicated records.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if log_format == "json" else ConsoleFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Route uvicorn through the root logger for one output format.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True

    # Our middleware logs access with correlation ids instead.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.propagate = False
