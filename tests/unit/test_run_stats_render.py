from datetime import UTC, datetime, timedelta

from scripts.run_stats import render

from app.application.runs.health import RunHealthReport
from app.infrastructure.database.models import RunKind
from app.infrastructure.database.repositories.run import (
    ModelCallFailureGroup,
    NodeHealth,
    RunFailureGroup,
    RunKindHealth,
)

UNTIL = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def report(**overrides: object) -> RunHealthReport:
    values: dict[str, object] = {
        "since": UNTIL - timedelta(hours=24),
        "until": UNTIL,
        "kind": None,
        "kinds": [RunKindHealth(RunKind.ANALYSIS, 4, 1, 0, 12.0, 40.0, 5)],
        "nodes": [NodeHealth(RunKind.ANALYSIS, "generate_analysis", 4, 1, 0, 9.0, 30.0, 31.0)],
        "failures": [
            RunFailureGroup(
                RunKind.WEEK_PLAN,
                None,
                "IntegrityError",
                "UniqueViolationError",
                "app/application/planning/service.py:170 in _generate",
                2,
                UNTIL,
            )
        ],
        "model_call_failures": [
            ModelCallFailureGroup(
                RunKind.ANALYSIS, "generate_analysis", "analysis.initial", "LLMTimeoutError", 3
            )
        ],
    }
    values.update(overrides)
    return RunHealthReport(**values)  # type: ignore[arg-type]


class TestRendering:
    def test_every_section_is_shown_with_rates(self):
        text = render(report())

        assert "Run health, all kinds: 2026-11-11 12:00 to 2026-11-12 12:00 UTC" in text
        assert "25.0%" in text, "one failure in four runs"
        assert "generate_analysis" in text
        assert "(outside nodes)" in text
        assert "UniqueViolationError" in text
        assert "app/application/planning/service.py:170 in _generate" in text
        assert "LLMTimeoutError" in text

    def test_empty_sections_say_so(self):
        text = render(
            report(kind=RunKind.ANALYSIS, kinds=[], nodes=[], failures=[], model_call_failures=[])
        )

        assert "Run health, analysis:" in text
        assert text.count("(none)") == 4
