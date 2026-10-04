"""Per-run execution traces: which steps ran, for how long, and what they cost.

A trace is bound to the current context rather than passed around. Nodes and
model providers are shared across requests and know nothing about which run
they serve; a context variable lets each record into the run it belongs to
without every signature carrying a trace parameter. LangGraph runs each node in
a task created from the caller's context, so a trace bound before the graph is
invoked is visible inside every node.

Traces hold metadata only: names, timings, outcomes, token counts. Nothing a
user wrote or a model generated is recorded here.
"""

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class SpanStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"


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
    model_calls: list[ModelCallTrace] = field(default_factory=list)

    # Node output, kept in memory only for summarising; never persisted.
    update: dict[str, Any] | None = None


@dataclass
class RunTrace:
    run_id: uuid.UUID
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
    never changes what the run does.
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
    except BaseException as error:
        step.status = SpanStatus.FAILED
        # Type only; messages may contain user data.
        step.error_type = type(error).__name__
        raise
    finally:
        step.duration_ms = (time.perf_counter() - started) * 1000
        _current_step.reset(token)

    if isinstance(result, dict):
        step.update = result
    return result


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
