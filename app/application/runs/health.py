"""How graph runs and their nodes are doing, across every user, over a window.

The per-run trace answers "what happened in this run". This answers the
operator's questions: which node fails, how often, why, and where; which steps
are slow; and which model calls fail even when the run survives them. It reads
only aggregates of run metadata, so it holds nothing a user wrote.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.infrastructure.database.models import RunKind
from app.infrastructure.database.repositories.run import (
    ModelCallFailureGroup,
    NodeHealth,
    RunFailureGroup,
    RunKindHealth,
    RunRepository,
)

MAX_WINDOW = timedelta(days=90)
MAX_GROUPS = 200


@dataclass(frozen=True)
class RunHealthReport:
    since: datetime
    until: datetime
    kind: RunKind | None
    kinds: list[RunKindHealth]
    nodes: list[NodeHealth]
    failures: list[RunFailureGroup]
    model_call_failures: list[ModelCallFailureGroup]

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready form, for feeding a dashboard or an alert."""
        document: dict[str, Any] = _jsonable(asdict(self))
        return document


class RunHealthService:
    def __init__(self, runs: RunRepository, clock: Callable[[], datetime] | None = None) -> None:
        self._runs = runs
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    async def report(
        self, *, window: timedelta, kind: RunKind | None = None, limit: int = 20
    ) -> RunHealthReport:
        if not timedelta(0) < window <= MAX_WINDOW:
            raise ValueError(f"window must be positive and at most {MAX_WINDOW.days} days")
        if not 1 <= limit <= MAX_GROUPS:
            raise ValueError(f"limit must be between 1 and {MAX_GROUPS}")

        until = self._clock()
        since = until - window
        return RunHealthReport(
            since=since,
            until=until,
            kind=kind,
            kinds=await self._runs.health_by_kind(since, kind),
            nodes=await self._runs.health_by_node(since, kind),
            failures=await self._runs.failure_groups(since, kind, limit=limit),
            model_call_failures=await self._runs.model_call_failures(since, kind, limit=limit),
        )


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, RunKind):
        return value.value
    if isinstance(value, float):
        return round(value, 2)
    return value
