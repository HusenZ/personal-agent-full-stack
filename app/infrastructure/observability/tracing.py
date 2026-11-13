"""Per-run execution traces: which steps ran, for how long, and what they cost.

A trace is bound to the current context rather than passed around. Nodes and
model providers are shared across requests and know nothing about which run
they serve; a context variable lets each record into the run it belongs to
without every signature carrying a trace parameter. LangGraph runs each node in
a task created from the caller's context, so a trace bound before the graph is
invoked is visible inside every node.

Traces hold metadata only: names, timings, outcomes, token counts. Nothing a
user wrote or a model generated is recorded here. A failure is described by
the types in its exception chain and the line of application code it surfaced
at, never by an exception message, which can carry user data.
"""

import asyncio
import logging
import time
import traceback
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any

logger = logging.getLogger(__name__)

# Causes recorded per failure; a deeper chain is a framework re-wrapping itself.
MAX_ERROR_CHAIN = 5
MAX_LOCATION_LENGTH = 300

_APP_DIR = Path(__file__).resolve().parents[2]
# Tracing wrappers only observe and re-raise, so they are never where a failure is.
_TRANSPARENT_FILES = frozenset(
    {
        Path(__file__).resolve(),
        _APP_DIR / "infrastructure" / "llm" / "tracing.py",
    }
)


class SpanStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    # Stopped from outside (a timeout, a disconnected client), not broken.
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ErrorInfo:
    """What may be recorded about an exception: its shape, never its message."""

    type: str
    # The types it was raised from, outermost first, excluding ``type`` itself.
    chain: tuple[str, ...] = ()
    # "app/<path>.py:<line> in <function>", the innermost application frame.
    location: str | None = None


def describe_error(error: BaseException) -> ErrorInfo:
    """Summarise an exception for a trace or a log line.

    The chain follows ``raise ... from`` first and implicit context otherwise,
    as a traceback does, so a provider failure wrapped in a domain error keeps
    its root cause. The location is the innermost frame inside the ``app``
    package: the line of our code where the failure surfaced, even when it was
    raised deep inside a driver or framework. It is taken from the outermost
    error in the chain that passed through our code. Tracing's own frames, here and in
    the model-call wrappers, are skipped, since they only ever re-raise.
    """
    causes = _causes(error)
    # The outermost error raised through our code; a wrapper raised outside it
    # (a framework, a test double) defers to the cause that did pass through.
    location = next(
        (
            found
            for item in (error, *causes)
            if (found := _app_location(item.__traceback__)) is not None
        ),
        None,
    )
    return ErrorInfo(
        type=type(error).__name__,
        chain=tuple(type(cause).__name__ for cause in causes),
        location=location,
    )


def _causes(error: BaseException) -> list[BaseException]:
    causes: list[BaseException] = []
    seen = {id(error)}
    current = error
    while len(causes) < MAX_ERROR_CHAIN:
        following = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
        if following is None or id(following) in seen:
            break
        seen.add(id(following))
        causes.append(following)
        current = following
    return causes


def _app_location(tb: TracebackType | None) -> str | None:
    location: str | None = None
    for frame, line in traceback.walk_tb(tb):
        path = Path(frame.f_code.co_filename).resolve()
        if path in _TRANSPARENT_FILES or not path.is_relative_to(_APP_DIR):
            continue
        relative = path.relative_to(_APP_DIR.parent).as_posix()
        location = f"{relative}:{line} in {frame.f_code.co_name}"
    return location[:MAX_LOCATION_LENGTH] if location is not None else None


class ModelCallKind(StrEnum):
    COMPLETION = "completion"
    STRUCTURED = "structured"
    EMBEDDING = "embedding"


@dataclass
class ModelCallTrace:
    kind: ModelCallKind
    name: str
    version: str | None
    duration_ms: float = 0.0
    status: SpanStatus = SpanStatus.OK
    error_type: str | None = None

    # Summed across retries; None means not reported, not zero.
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class StepTrace:
    node: str
    offset_ms: float
    duration_ms: float = 0.0
    status: SpanStatus = SpanStatus.OK
    error_type: str | None = None
    error_chain: tuple[str, ...] = ()
    error_location: str | None = None
    model_calls: list[ModelCallTrace] = field(default_factory=list)

    # Node output, kept in memory only for summarising; never persisted.
    update: dict[str, Any] | None = None


@dataclass
class RunTrace:
    run_id: uuid.UUID
    # What kind of run this is, for log lines emitted while it runs.
    kind: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    steps: list[StepTrace] = field(default_factory=list)

    # Model calls outside any node, so nothing billed goes unrecorded.
    other_calls: list[ModelCallTrace] = field(default_factory=list)
    _started: float = field(default_factory=time.perf_counter)

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._started) * 1000

    def model_calls(self) -> list[ModelCallTrace]:
        return [call for step in self.steps for call in step.model_calls] + self.other_calls

    def failed_step(self) -> StepTrace | None:
        """The step the run failed in; a cancelled step did not fail."""
        return next((step for step in self.steps if step.status is SpanStatus.FAILED), None)


_current_trace: ContextVar[RunTrace | None] = ContextVar("current_trace", default=None)
_current_step: ContextVar[StepTrace | None] = ContextVar("current_step", default=None)
_current_call: ContextVar[ModelCallTrace | None] = ContextVar("current_call", default=None)


@contextmanager
def bind_trace(trace: RunTrace) -> Iterator[RunTrace]:
    token = _current_trace.set(trace)
    try:
        yield trace
    finally:
        _current_trace.reset(token)


def current_trace() -> RunTrace | None:
    return _current_trace.get()


async def run_step[ResultT](node: str, call: Callable[[], Awaitable[ResultT]]) -> ResultT:
    """Run one graph step, recording it into the current trace if there is one.

    A failure is recorded and then re-raised: the trace observes the run, it
    never changes what the run does. It is also logged at once, so the failing
    node is on record even if the run's own record can never be written, which
    is most likely exactly when the database is what failed.
    """
    trace = _current_trace.get()
    if trace is None:
        return await call()

    step = StepTrace(node=node, offset_ms=trace.elapsed_ms())
    trace.steps.append(step)
    token = _current_step.set(step)
    started = time.perf_counter()
    try:
        result = await call()
    except asyncio.CancelledError:
        step.duration_ms = _since(started)
        step.status = SpanStatus.CANCELLED
        logger.info("graph_step_cancelled", extra=_step_fields(trace, step))
        raise
    except BaseException as error:
        step.duration_ms = _since(started)
        info = describe_error(error)
        step.status = SpanStatus.FAILED
        step.error_type = info.type
        step.error_chain = info.chain
        step.error_location = info.location
        logger.warning(
            "graph_step_failed",
            extra={
                **_step_fields(trace, step),
                "error_type": info.type,
                "error_chain": list(info.chain),
                "error_location": info.location,
            },
        )
        raise
    finally:
        _current_step.reset(token)

    step.duration_ms = _since(started)
    logger.debug("graph_step_completed", extra=_step_fields(trace, step))
    if isinstance(result, dict):
        step.update = result
    return result


def _since(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _step_fields(trace: RunTrace, step: StepTrace) -> dict[str, Any]:
    return {
        "run_kind": trace.kind,
        "node": step.node,
        "duration_ms": round(step.duration_ms, 2),
        "model_calls": len(step.model_calls),
    }


@asynccontextmanager
async def model_call(
    kind: ModelCallKind, name: str, version: str | None = None
) -> AsyncIterator[ModelCallTrace]:
    """Record one model call into the current step, or the run if outside one."""
    span = ModelCallTrace(kind=kind, name=name, version=version)
    trace = _current_trace.get()
    if trace is not None:
        step = _current_step.get()
        (step.model_calls if step is not None else trace.other_calls).append(span)

    token = _current_call.set(span)
    started = time.perf_counter()
    try:
        yield span
    except asyncio.CancelledError:
        span.status = SpanStatus.CANCELLED
        raise
    except BaseException as error:
        span.status = SpanStatus.FAILED
        span.error_type = type(error).__name__
        raise
    finally:
        span.duration_ms = (time.perf_counter() - started) * 1000
        _current_call.reset(token)


def record_token_usage(input_tokens: int | None, output_tokens: int | None) -> None:
    """Add one attempt's usage to the model call in progress, if any."""
    span = _current_call.get()
    if span is None:
        return
    if input_tokens is not None:
        span.input_tokens = (span.input_tokens or 0) + input_tokens
    if output_tokens is not None:
        span.output_tokens = (span.output_tokens or 0) + output_tokens
