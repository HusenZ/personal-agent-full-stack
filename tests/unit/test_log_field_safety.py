"""Guards against a failure mode that only appears at runtime.

``logging`` raises ``KeyError`` when an ``extra`` field shadows an attribute
LogRecord sets itself. Several reserved names are ordinary domain words, so the
mistake is easy to make and invisible until the line actually runs — and, on a
background path, may be swallowed by the handler that catches everything.
"""

import ast
import pathlib

import pytest

from app.infrastructure.observability.logging import reserved_log_fields

APP_ROOT = pathlib.Path(__file__).resolve().parents[2] / "app"

# Reserved LogRecord attributes; shadowing them breaks formatting.
_ALWAYS_UNSAFE = {"created", "message", "asctime", "name", "args", "msg", "levelname"}


def log_extra_fields() -> list[tuple[pathlib.Path, int, str]]:
    """Every literal key passed as ``extra=`` anywhere in the application."""
    found: list[tuple[pathlib.Path, int, str]] = []

    for path in sorted(APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
                    continue
                for key in keyword.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        found.append((path, node.lineno, key.value))

    return found


class TestLogFieldSafety:
    def test_the_application_actually_logs_structured_fields(self):
        """A scan that finds nothing would pass vacuously forever."""
        assert len(log_extra_fields()) > 10

    def test_no_log_field_shadows_a_record_attribute(self):
        reserved = reserved_log_fields()

        offenders = [
            f"{path.relative_to(APP_ROOT)}:{line} uses extra={{'{field}': ...}}"
            for path, line, field in log_extra_fields()
            if field in reserved
        ]

        assert offenders == []

    @pytest.mark.parametrize("field", sorted(_ALWAYS_UNSAFE))
    def test_the_known_hazards_are_recognised(self, field: str):
        """Pins the guard itself: if this set stopped being reserved, the scan
        above would silently stop protecting anything."""
        assert field in reserved_log_fields()

    def test_a_reserved_field_really_does_raise(self):
        """Documents why this matters: it is an exception, not a warning.

        Exercises makeRecord directly rather than a level-gated call, so the
        assertion does not depend on how logging happens to be configured.
        """
        import logging

        logger = logging.getLogger("test.reserved")

        with pytest.raises(KeyError, match="created"):
            logger.makeRecord(
                logger.name,
                logging.INFO,
                __file__,
                1,
                "event",
                (),
                None,
                extra={"created": 1},
            )
