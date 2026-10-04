import uuid
from collections.abc import Callable
from typing import Any

from app.infrastructure.database.models import AgentRun, RunKind, RunStatus
from app.infrastructure.observability.tracing import ModelCallTrace, RunTrace, StepTrace

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
        error_type=type(error).__name__ if error is not None else None,
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
