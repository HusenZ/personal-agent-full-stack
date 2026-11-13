"""Report how graph runs and their nodes are doing, across every user.

    uv run python -m scripts.run_stats                     # the last 24 hours
    uv run python -m scripts.run_stats --hours 168 --kind analysis
    uv run python -m scripts.run_stats --json              # for a dashboard or an alert

Reads the database in DATABASE_URL. Shows aggregates of run metadata only:
node names, counts, timings, exception types and code locations. Nothing a
user wrote is read or printed.
"""

import argparse
import asyncio
import json
from datetime import timedelta

from app.application.runs.health import MAX_GROUPS, MAX_WINDOW, RunHealthReport, RunHealthService
from app.core.config import get_settings
from app.infrastructure.database.models import RunKind
from app.infrastructure.database.repositories.run import RunRepository
from app.infrastructure.database.session import create_database_engine, create_session_factory


async def collect(window: timedelta, kind: RunKind | None, limit: int) -> RunHealthReport:
    engine = create_database_engine(get_settings())
    try:
        async with create_session_factory(engine)() as session:
            service = RunHealthService(RunRepository(session))
            return await service.report(window=window, kind=kind, limit=limit)
    finally:
        await engine.dispose()


def _rate(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "-"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return ["  (none)"]
    widths = [max(len(str(cell)) for cell in column) for column in zip(headers, *rows, strict=True)]

    def line(cells: list[str]) -> str:
        return (
            "  "
            + "  ".join(str(cell).ljust(width) for cell, width in zip(cells, widths, strict=True))
        ).rstrip()

    return [line(headers), line(["-" * width for width in widths]), *(line(row) for row in rows)]


def render(report: RunHealthReport) -> str:
    scope = report.kind.value if report.kind else "all kinds"
    lines = [
        f"Run health, {scope}: {report.since:%Y-%m-%d %H:%M} to {report.until:%Y-%m-%d %H:%M} UTC",
        "",
        "Runs",
        *_table(
            ["kind", "runs", "failed", "fail %", "degraded", "p50 ms", "p95 ms", "model calls"],
            [
                [
                    item.kind.value,
                    str(item.runs),
                    str(item.failed),
                    _rate(item.failed, item.runs),
                    str(item.degraded),
                    f"{item.p50_ms:.1f}",
                    f"{item.p95_ms:.1f}",
                    str(item.model_calls),
                ]
                for item in report.kinds
            ],
        ),
        "",
        "Nodes",
        *_table(
            ["kind", "node", "runs", "failed", "fail %", "cancelled", "p50 ms", "p95 ms", "max ms"],
            [
                [
                    item.kind.value,
                    item.node,
                    str(item.executions),
                    str(item.failed),
                    _rate(item.failed, item.executions),
                    str(item.cancelled),
                    f"{item.p50_ms:.1f}",
                    f"{item.p95_ms:.1f}",
                    f"{item.max_ms:.1f}",
                ]
                for item in report.nodes
            ],
        ),
        "",
        "Failed runs, by where and why",
        *_table(
            ["runs", "kind", "node", "error", "root cause", "location", "last seen"],
            [
                [
                    str(item.runs),
                    item.kind.value,
                    item.failed_node or "(outside nodes)",
                    item.error_type or "-",
                    item.root_cause or "-",
                    item.error_location or "-",
                    f"{item.last_seen:%Y-%m-%d %H:%M}",
                ]
                for item in report.failures
            ],
        ),
        "",
        "Failed model calls, including ones the run survived",
        *_table(
            ["calls", "kind", "node", "call", "error"],
            [
                [str(item.calls), item.kind.value, item.node, item.call, item.error_type or "-"]
                for item in report.model_call_failures
            ],
        ),
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=24.0, help="window to report on")
    parser.add_argument("--kind", type=RunKind, choices=list(RunKind), default=None)
    parser.add_argument("--limit", type=int, default=20, help="failure groups to show")
    parser.add_argument("--json", action="store_true", help="print JSON instead of tables")
    arguments = parser.parse_args()

    window = timedelta(hours=arguments.hours)
    if not timedelta(0) < window <= MAX_WINDOW:
        parser.error(f"--hours must be positive and at most {MAX_WINDOW.days * 24}")
    if not 1 <= arguments.limit <= MAX_GROUPS:
        parser.error(f"--limit must be between 1 and {MAX_GROUPS}")

    report = asyncio.run(collect(window, arguments.kind, arguments.limit))
    print(json.dumps(report.to_dict(), indent=2) if arguments.json else render(report))


if __name__ == "__main__":
    main()
