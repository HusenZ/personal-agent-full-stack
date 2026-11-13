import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import AgentRun, RunKind, RunStatus
from app.infrastructure.database.repositories.run import RunRepository
from app.infrastructure.observability.tracing import (
    ModelCallTrace,
    RunTrace,
    StepTrace,
    describe_error,
)

logger = logging.getLogger(__name__)

# Picks what may be stored about a step; None if only timing matters.
type StepDescriber = Callable[[str, dict[str, Any] | None], dict[str, Any] | None]


def build_run_record(
    trace: RunTrace,
    *,
    kind: RunKind,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    request_id: str | None,
    result: dict[str, Any],
    error: BaseException | None,
    describe: StepDescriber,
) -> AgentRun:
    """Turn an in-memory trace into the run's durable record.

    Step detail comes from ``describe`` alone, which for conversation turns is
    the same allow-list that decides what a streaming client may see. Raw node
    output is dropped here and never reaches storage.
    """
    calls = trace.model_calls()
    failed = trace.failed_step()
    info = describe_error(error) if error is not None else None

    return AgentRun(
        id=trace.run_id,
        user_id=user_id,
        conversation_id=conversation_id,
        kind=kind,
        status=RunStatus.FAILED if error is not None else RunStatus.COMPLETED,
        request_id=request_id,
        started_at=trace.started_at,
        duration_ms=round(trace.elapsed_ms(), 2),
        model_calls=len(calls),
        input_tokens=_sum_reported(call.input_tokens for call in calls),
        output_tokens=_sum_reported(call.output_tokens for call in calls),
        retrieval_attempts=int(result.get("retrieval_attempts") or 0),
        generation_attempts=int(result.get("generation_attempts") or 0),
        grounded=result.get("grounded"),
        degraded=bool(result.get("degraded")),
        error_type=info.type if info is not None else None,
        error_chain=list(info.chain) if info is not None else None,
        error_location=info.location if info is not None else None,
        failed_node=failed.node if failed is not None else None,
        steps=[_step_payload(step, describe) for step in trace.steps],
    )


def _step_payload(step: StepTrace, describe: StepDescriber) -> dict[str, Any]:
    return {
        "node": step.node,
        "offset_ms": round(step.offset_ms, 2),
        "duration_ms": round(step.duration_ms, 2),
        "status": step.status.value,
        "error_type": step.error_type,
        "error_chain": list(step.error_chain),
        "error_location": step.error_location,
        "detail": (describe(step.node, step.update) or {}) if step.update is not None else {},
        "model_calls": [_call_payload(call) for call in step.model_calls],
    }


def _call_payload(call: ModelCallTrace) -> dict[str, Any]:
    return {
        "kind": call.kind.value,
        "name": call.name,
        "version": call.version,
        "duration_ms": round(call.duration_ms, 2),
        "status": call.status.value,
        "error_type": call.error_type,
        "input_tokens": call.input_tokens,
        "output_tokens": call.output_tokens,
    }


def _sum_reported(values: Any) -> int | None:
    reported = [value for value in values if value is not None]
    return sum(reported) if reported else None


async def store_failed_run(session: AsyncSession, runs: RunRepository, record: AgentRun) -> None:
    """Store the record of a run that failed, on its own, after a rollback.

    Whatever the run was writing is rolled back first, so only the record
    survives. Best effort: when the database is what failed, this fails too,
    and the original error is the one that matters to the caller. Either way
    ``agent_run_failed`` is logged, one event for every kind of run, so the
    failure is on record even when the row is not.
    """
    # Read before any rollback; the record must not be touched after one.
    fields = {
        "run_id": str(record.id),
        "run_kind": record.kind.value,
        "user_id": str(record.user_id),
        "failed_node": record.failed_node,
        "error_type": record.error_type,
        "error_chain": record.error_chain,
        "error_location": record.error_location,
        "duration_ms": record.duration_ms,
    }
    stored = True
    try:
        await session.rollback()
        runs.add(record)
        await session.commit()
    except Exception:
        stored = False
        logger.exception("run_record_failed", extra={"run_id": fields["run_id"]})
        await session.rollback()

    logger.warning("agent_run_failed", extra={**fields, "run_stored": stored})
