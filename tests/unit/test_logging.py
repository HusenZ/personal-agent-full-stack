import json
import logging

from app.core.context import bind_request_id
from app.infrastructure.observability.logging import ConsoleFormatter, JsonFormatter


def _record(message: str = "event_occurred", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )
    record.__dict__.update(extra)
    return record


def test_json_formatter_emits_expected_envelope():
    payload = json.loads(JsonFormatter().format(_record()))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["event"] == "event_occurred"
    assert "timestamp" in payload


def test_json_formatter_includes_extra_fields():
    payload = json.loads(JsonFormatter().format(_record(http_status=200, duration_ms=12.5)))

    assert payload["http_status"] == 200
    assert payload["duration_ms"] == 12.5


def test_json_formatter_includes_bound_request_id():
    bind_request_id("req-abc")

    payload = json.loads(JsonFormatter().format(_record()))

    assert payload["request_id"] == "req-abc"


def test_json_formatter_serialises_unexpected_types():
    payload = json.loads(JsonFormatter().format(_record(value=object())))

    assert isinstance(payload["value"], str)


def test_console_formatter_renders_extras():
    bind_request_id("req-xyz")

    line = ConsoleFormatter().format(_record(http_status=404))

    assert "req-xyz" in line
    assert "http_status=404" in line


def test_uvicorn_colour_duplicate_is_dropped():
    payload = json.loads(JsonFormatter().format(_record(color_message="\x1b[36mnoise\x1b[0m")))

    assert "color_message" not in payload
