"""How a failing node is described, recorded and logged."""

import asyncio
import json
import logging
import uuid
from typing import Any

import pytest

from app.application.runs.recording import build_run_record, store_failed_run
from app.graph.analysis.nodes.build_evidence import build_evidence
from app.infrastructure.database.models import AgentRun, RunKind, RunStatus
from app.infrastructure.llm.exceptions import LLMUnavailableError
from app.infrastructure.observability.logging import JsonFormatter
from app.infrastructure.observability.tracing import (
    MAX_ERROR_CHAIN,
    ModelCallKind,
    RunTrace,
    SpanStatus,
    bind_trace,
    describe_error,
    model_call,
    run_step,
)

SECRET = "user said: my salary is 90k"


class DomainError(Exception):
    pass


def raised(error: BaseException) -> BaseException:
    try:
        raise error
    except BaseException as caught:
        return caught


def wrapped(outer: BaseException, inner: BaseException) -> BaseException:
    try:
        try:
            raise inner
        except BaseException as cause:
            raise outer from cause
    except BaseException as caught:
        return caught


async def app_failure() -> dict[str, Any]:
    """A failure raised inside application code: evidence built from a bad state."""
    await build_evidence({"raw_evidence": "not evidence"})  # type: ignore[typeddict-item]
    return {}


class TestDescribingAnError:
    def test_the_type_is_recorded_and_the_message_is_not(self):
        info = describe_error(raised(RuntimeError(SECRET)))

        assert info.type == "RuntimeError"
        assert SECRET not in repr(info)

    def test_an_explicit_cause_is_the_chain(self):
        info = describe_error(wrapped(DomainError(), LLMUnavailableError(SECRET)))

        assert info.type == "DomainError"
        assert info.chain == ("LLMUnavailableError",)

    def test_implicit_context_is_followed_too(self):
        def fails_while_handling() -> BaseException:
            try:
                try:
                    raise ConnectionError(SECRET)
                except ConnectionError:
                    # Raised while handling, without "from": the context is implicit.
                    raise KeyError("x")  # noqa: B904
            except BaseException as caught:
                return caught

        assert describe_error(fails_while_handling()).chain == ("ConnectionError",)

    def test_suppressed_context_is_not_reported(self):
        def suppressed() -> BaseException:
            try:
                try:
                    raise ConnectionError(SECRET)
                except ConnectionError:
                    raise DomainError() from None
            except BaseException as caught:
                return caught

        assert describe_error(suppressed()).chain == ()

    def test_a_long_chain_is_bounded(self):
        error: BaseException = ValueError("root")
        for _ in range(MAX_ERROR_CHAIN + 5):
            error = wrapped(DomainError(), error)

        assert len(describe_error(error).chain) == MAX_ERROR_CHAIN

    def test_a_cyclic_chain_terminates(self):
        first, second = DomainError(), ValueError()
        first.__cause__, second.__cause__ = second, first

        assert describe_error(first).chain == ("ValueError",)

    async def test_the_location_is_the_innermost_application_frame(self):
        with pytest.raises(AttributeError) as caught:
            await app_failure()

        location = describe_error(caught.value).location

        assert location is not None
        assert location.startswith("app/domain/analysis/evidence.py:")
        assert location.endswith(" in build_evidence_bundle")

    def test_a_failure_outside_application_code_has_no_location(self):
        assert describe_error(raised(RuntimeError())).location is None

    async def test_the_location_never_points_at_the_tracing_wrapper(self):
        async def test_node() -> dict[str, Any]:
            raise RuntimeError(SECRET)

        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            with pytest.raises(RuntimeError):
                await run_step("node", test_node)

        assert trace.steps[0].error_location is None


class TestFailingSteps:
    async def test_a_failed_step_carries_its_chain_and_location(self):
        async def node() -> dict[str, Any]:
            try:
                await app_failure()
            except AttributeError as error:
                raise DomainError() from error
            return {}

        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            with pytest.raises(DomainError):
                await run_step("generate", node)

        (step,) = trace.steps
        assert step.status is SpanStatus.FAILED
        assert step.error_type == "DomainError"
        assert step.error_chain == ("AttributeError",)
        assert step.error_location is not None
        assert step.error_location.startswith("app/domain/analysis/evidence.py:")
        assert step.duration_ms >= 0

    async def test_a_cancelled_step_is_not_a_failure(self):
        async def slow() -> dict[str, Any]:
            await asyncio.sleep(10)
            return {}

        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(run_step("generate_reply", slow), 0.01)

        (step,) = trace.steps
        assert step.status is SpanStatus.CANCELLED
        assert step.error_type is None
        assert step.duration_ms > 0
        assert trace.failed_step() is None

    async def test_a_cancelled_model_call_is_not_a_failure(self):
        async def calls_slowly() -> dict[str, Any]:
            async with model_call(ModelCallKind.STRUCTURED, "analysis.initial", "1"):
                await asyncio.sleep(10)
            return {}

        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(run_step("generate", calls_slowly), 0.01)

        (call,) = trace.model_calls()
        assert call.status is SpanStatus.CANCELLED
        assert call.error_type is None

    async def test_the_failed_step_is_the_first_failure_not_a_later_cancellation(self):
        async def fails() -> dict[str, Any]:
            raise DomainError()

        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            with pytest.raises(DomainError):
                await run_step("first", fails)

        failed = trace.failed_step()
        assert failed is not None and failed.node == "first"


class TestLoggingAtTheMomentOfFailure:
    async def test_a_failed_step_is_logged_with_where_and_why(
        self, caplog: pytest.LogCaptureFixture
    ):
        async def node() -> dict[str, Any]:
            raise DomainError(SECRET) from LLMUnavailableError(SECRET)

        with caplog.at_level(logging.DEBUG, logger="app.infrastructure.observability.tracing"):
            with bind_trace(RunTrace(run_id=uuid.uuid4(), kind="analysis")):
                with pytest.raises(DomainError):
                    await run_step("generate_analysis", node)

        (record,) = [r for r in caplog.records if r.getMessage() == "graph_step_failed"]
        assert record.levelno == logging.WARNING
        assert record.node == "generate_analysis"  # type: ignore[attr-defined]
        assert record.run_kind == "analysis"  # type: ignore[attr-defined]
        assert record.error_type == "DomainError"  # type: ignore[attr-defined]
        assert record.error_chain == ["LLMUnavailableError"]  # type: ignore[attr-defined]
        assert record.duration_ms >= 0  # type: ignore[attr-defined]
        assert SECRET not in JsonFormatter().format(record)

    async def test_a_completed_step_is_logged_at_debug(self, caplog: pytest.LogCaptureFixture):
        async def node() -> dict[str, Any]:
            return {"reply": SECRET}

        with caplog.at_level(logging.DEBUG, logger="app.infrastructure.observability.tracing"):
            with bind_trace(RunTrace(run_id=uuid.uuid4(), kind="analysis")):
                await run_step("build_evidence", node)

        (record,) = [r for r in caplog.records if r.getMessage() == "graph_step_completed"]
        assert record.levelno == logging.DEBUG
        assert SECRET not in JsonFormatter().format(record)

    async def test_a_cancelled_step_is_logged_as_cancelled(self, caplog: pytest.LogCaptureFixture):
        async def slow() -> dict[str, Any]:
            await asyncio.sleep(10)
            return {}

        with caplog.at_level(logging.INFO, logger="app.infrastructure.observability.tracing"):
            with bind_trace(RunTrace(run_id=uuid.uuid4())):
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(run_step("slow", slow), 0.01)

        messages = [r.getMessage() for r in caplog.records]
        assert "graph_step_cancelled" in messages
        assert "graph_step_failed" not in messages

    def test_every_log_line_in_a_run_names_its_kind(self):
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "event", None, None)

        with bind_trace(RunTrace(run_id=uuid.uuid4(), kind="onboarding_turn")) as trace:
            payload = json.loads(JsonFormatter().format(record))

        assert payload["run_id"] == str(trace.run_id)
        assert payload["run_kind"] == "onboarding_turn"


class TestRecordingTheRun:
    def record(self, trace: RunTrace, error: BaseException | None) -> AgentRun:
        return build_run_record(
            trace,
            kind=RunKind.ANALYSIS,
            user_id=uuid.uuid4(),
            conversation_id=None,
            request_id=None,
            result={},
            error=error,
            describe=lambda node, update: None,
        )

    async def test_a_failed_run_stores_its_chain_location_and_step_detail(self):
        async def node() -> dict[str, Any]:
            try:
                await app_failure()
            except AttributeError as error:
                raise DomainError() from error
            return {}

        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            with pytest.raises(DomainError) as caught:
                await run_step("generate_analysis", node)

        record = self.record(trace, caught.value)

        assert record.status is RunStatus.FAILED
        assert record.failed_node == "generate_analysis"
        assert record.error_type == "DomainError"
        assert record.error_chain == ["AttributeError"]
        assert record.error_location is not None
        step = record.steps[0]
        assert step["error_chain"] == ["AttributeError"]
        assert step["error_location"] == record.error_location
        assert SECRET not in json.dumps(record.steps)

    def test_a_successful_run_has_no_failure_fields(self):
        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            pass

        record = self.record(trace, None)

        assert (record.error_type, record.error_chain, record.error_location) == (None, None, None)


class FakeSession:
    def __init__(self, *, commit_fails: bool = False) -> None:
        self.commit_fails = commit_fails
        self.rollbacks = 0
        self.commits = 0

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def commit(self) -> None:
        if self.commit_fails:
            raise ConnectionError("database is down")
        self.commits += 1


class FakeRuns:
    def __init__(self) -> None:
        self.added: list[AgentRun] = []

    def add(self, run: AgentRun) -> None:
        self.added.append(run)


def failed_record() -> AgentRun:
    with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
        pass
    return build_run_record(
        trace,
        kind=RunKind.WEEK_PLAN,
        user_id=uuid.uuid4(),
        conversation_id=None,
        request_id=None,
        result={},
        error=raised(wrapped(DomainError(), LLMUnavailableError())),
        describe=lambda node, update: None,
    )


class TestStoringAFailedRun:
    async def test_the_work_is_rolled_back_and_the_record_committed(
        self, caplog: pytest.LogCaptureFixture
    ):
        session, runs, record = FakeSession(), FakeRuns(), failed_record()

        with caplog.at_level(logging.WARNING):
            await store_failed_run(session, runs, record)  # type: ignore[arg-type]

        assert session.rollbacks == 1
        assert session.commits == 1
        assert runs.added == [record]
        (event,) = [r for r in caplog.records if r.getMessage() == "agent_run_failed"]
        assert event.run_kind == "week_plan"  # type: ignore[attr-defined]
        assert event.error_type == "DomainError"  # type: ignore[attr-defined]
        assert event.error_chain == ["LLMUnavailableError"]  # type: ignore[attr-defined]
        assert event.run_stored is True  # type: ignore[attr-defined]

    async def test_when_the_record_cannot_be_stored_the_failure_is_still_logged(
        self, caplog: pytest.LogCaptureFixture
    ):
        session, runs, record = FakeSession(commit_fails=True), FakeRuns(), failed_record()

        with caplog.at_level(logging.WARNING):
            await store_failed_run(session, runs, record)  # type: ignore[arg-type]

        messages = [r.getMessage() for r in caplog.records]
        assert "run_record_failed" in messages
        (event,) = [r for r in caplog.records if r.getMessage() == "agent_run_failed"]
        assert event.run_stored is False  # type: ignore[attr-defined]
        assert event.run_id == str(record.id)  # type: ignore[attr-defined]
