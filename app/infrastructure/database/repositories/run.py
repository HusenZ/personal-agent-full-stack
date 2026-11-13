import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Float, Select, and_, cast, column, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import AgentRun, RunKind, RunStatus


@dataclass(frozen=True)
class RunKindHealth:
    kind: RunKind
    runs: int
    failed: int
    degraded: int
    p50_ms: float
    p95_ms: float
    model_calls: int


@dataclass(frozen=True)
class NodeHealth:
    kind: RunKind
    node: str
    executions: int
    failed: int
    cancelled: int
    p50_ms: float
    p95_ms: float
    max_ms: float


@dataclass(frozen=True)
class RunFailureGroup:
    """Failed runs that failed the same way, in the same place."""

    kind: RunKind
    # None when the run failed outside every node.
    failed_node: str | None
    error_type: str | None
    root_cause: str | None
    error_location: str | None
    runs: int
    last_seen: datetime


@dataclass(frozen=True)
class ModelCallFailureGroup:
    """Model calls that failed inside a step, whether or not the run survived it."""

    kind: RunKind
    node: str
    call: str
    error_type: str | None
    calls: int


class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, run: AgentRun) -> None:
        self._session.add(run)

    async def get_owned_by(self, run_id: uuid.UUID, user_id: uuid.UUID) -> AgentRun | None:
        """Load a run only if it belongs to this user, in one query."""
        statement = select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user_id)
        return (await self._session.execute(statement)).scalar_one_or_none()

    # Operational reads. They span every user, return aggregates only, and are
    # never reachable from a user-facing endpoint.

    async def health_by_kind(self, since: datetime, kind: RunKind | None) -> list[RunKindHealth]:
        duration = AgentRun.duration_ms
        statement = _in_window(
            select(
                AgentRun.kind,
                func.count(),
                func.count().filter(AgentRun.status == RunStatus.FAILED),
                func.count().filter(AgentRun.degraded.is_(True)),
                _percentile(0.5, duration),
                _percentile(0.95, duration),
                func.coalesce(func.sum(AgentRun.model_calls), 0),
            ),
            since,
            kind,
        ).group_by(AgentRun.kind)

        rows = (await self._session.execute(statement.order_by(AgentRun.kind))).all()
        return [
            RunKindHealth(
                kind=row[0],
                runs=row[1],
                failed=row[2],
                degraded=row[3],
                p50_ms=float(row[4] or 0.0),
                p95_ms=float(row[5] or 0.0),
                model_calls=int(row[6]),
            )
            for row in rows
        ]

    async def health_by_node(self, since: datetime, kind: RunKind | None) -> list[NodeHealth]:
        step = _steps()
        node = step.c.value["node"].astext
        status = step.c.value["status"].astext
        duration = cast(step.c.value["duration_ms"].astext, Float)

        statement = (
            _in_window(
                select(
                    AgentRun.kind,
                    node,
                    func.count(),
                    func.count().filter(status == "failed"),
                    func.count().filter(status == "cancelled"),
                    _percentile(0.5, duration),
                    _percentile(0.95, duration),
                    func.max(duration),
                ).select_from(AgentRun.__table__.join(step, _always())),
                since,
                kind,
            )
            .group_by(AgentRun.kind, node)
            .order_by(AgentRun.kind, node)
        )

        rows = (await self._session.execute(statement)).all()
        return [
            NodeHealth(
                kind=row[0],
                node=row[1],
                executions=row[2],
                failed=row[3],
                cancelled=row[4],
                p50_ms=float(row[5] or 0.0),
                p95_ms=float(row[6] or 0.0),
                max_ms=float(row[7] or 0.0),
            )
            for row in rows
        ]

    async def failure_groups(
        self, since: datetime, kind: RunKind | None, *, limit: int
    ) -> list[RunFailureGroup]:
        # The last cause in the chain is the root: what the failure started as.
        root_cause = AgentRun.error_chain.op("->>")(-1)
        keys = (
            AgentRun.kind,
            AgentRun.failed_node,
            AgentRun.error_type,
            root_cause,
            AgentRun.error_location,
        )
        statement = (
            _in_window(select(*keys, func.count(), func.max(AgentRun.started_at)), since, kind)
            .where(AgentRun.status == RunStatus.FAILED)
            .group_by(*keys)
            .order_by(func.count().desc(), func.max(AgentRun.started_at).desc())
            .limit(limit)
        )

        rows = (await self._session.execute(statement)).all()
        return [
            RunFailureGroup(
                kind=row[0],
                failed_node=row[1],
                error_type=row[2],
                root_cause=row[3],
                error_location=row[4],
                runs=row[5],
                last_seen=row[6],
            )
            for row in rows
        ]

    async def model_call_failures(
        self, since: datetime, kind: RunKind | None, *, limit: int
    ) -> list[ModelCallFailureGroup]:
        step = _steps()
        call = (
            func.jsonb_array_elements(step.c.value["model_calls"])
            .table_valued(column("value", JSONB))
            .lateral("call")
        )
        keys = (
            AgentRun.kind,
            step.c.value["node"].astext,
            call.c.value["name"].astext,
            call.c.value["error_type"].astext,
        )
        statement = (
            _in_window(
                select(*keys, func.count()).select_from(
                    AgentRun.__table__.join(step, _always()).join(call, _always())
                ),
                since,
                kind,
            )
            .where(call.c.value["status"].astext == "failed")
            .group_by(*keys)
            .order_by(func.count().desc())
            .limit(limit)
        )

        rows = (await self._session.execute(statement)).all()
        return [
            ModelCallFailureGroup(
                kind=row[0], node=row[1], call=row[2], error_type=row[3], calls=row[4]
            )
            for row in rows
        ]


def _steps() -> Any:
    """Each element of a run's ``steps`` array as a row, joined laterally."""
    return (
        func.jsonb_array_elements(AgentRun.steps)
        .table_valued(column("value", JSONB))
        .lateral("step")
    )


def _always() -> Any:
    # A lateral join carries its own correlation; the ON clause is a formality.
    return and_(True)


def _percentile(fraction: float, value: Any) -> Any:
    return func.percentile_cont(fraction).within_group(value)


def _in_window[RowT: tuple[Any, ...]](
    statement: Select[RowT], since: datetime, kind: RunKind | None
) -> Select[RowT]:
    statement = statement.where(AgentRun.started_at >= since)
    if kind is not None:
        statement = statement.where(AgentRun.kind == kind)
    return statement
