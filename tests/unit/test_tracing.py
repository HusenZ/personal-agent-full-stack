import asyncio
import json
import logging
import uuid

import pytest

from app.application.runs.recording import build_run_record
from app.infrastructure.database.models import RunKind, RunStatus
from app.infrastructure.observability.logging import JsonFormatter
from app.infrastructure.observability.tracing import (
    ModelCallKind,
    RunTrace,
    SpanStatus,
    bind_trace,
    current_trace,
    model_call,
    record_token_usage,
    run_step,
)

DRAFT = "Since you ran a marathon last spring, add a long run."


async def returning(value: dict[str, object]) -> dict[str, object]:
    return value


async def calling_the_model(name: str, tokens: tuple[int, int] | None = None) -> dict[str, object]:
    async with model_call(ModelCallKind.COMPLETION, name, "1"):
        if tokens is not None:
            record_token_usage(*tokens)
    return {}


class TestSteps:
    async def test_a_step_is_recorded_with_its_timing_and_output(self):
        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            await run_step("first", lambda: returning({"reply": DRAFT}))

        (step,) = trace.steps
        assert step.node == "first"
        assert step.status is SpanStatus.OK
        assert step.duration_ms >= 0
        assert step.update == {"reply": DRAFT}

    async def test_a_failing_step_is_recorded_and_the_error_still_raised(self):
        async def failing() -> dict[str, object]:
            raise RuntimeError("secret detail at 10.0.0.5")

        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            with pytest.raises(RuntimeError):
                await run_step("broken", failing)

        assert trace.steps[0].status is SpanStatus.FAILED
        assert trace.steps[0].error_type == "RuntimeError"
        assert trace.failed_step() is trace.steps[0]

    async def test_without_a_trace_steps_simply_run(self):
        assert current_trace() is None

        assert await run_step("anything", lambda: returning({"ok": True})) == {"ok": True}


class TestModelCalls:
    async def test_a_call_inside_a_step_belongs_to_that_step(self):
        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            await run_step("drafting", lambda: calling_the_model("conversation.reply"))

        assert [call.name for call in trace.steps[0].model_calls] == ["conversation.reply"]
        assert trace.other_calls == []

    async def test_a_call_outside_any_step_is_still_counted(self):
        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            await calling_the_model("stray")

        assert [call.name for call in trace.model_calls()] == ["stray"]

    async def test_a_failed_call_records_its_error_type(self):
        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            with pytest.raises(TimeoutError):
                async with model_call(ModelCallKind.STRUCTURED, "slow"):
                    raise TimeoutError

        assert trace.model_calls()[0].status is SpanStatus.FAILED
        assert trace.model_calls()[0].error_type == "TimeoutError"

    async def test_usage_outside_a_call_is_ignored(self):
        record_token_usage(10, 10)


class TestIsolation:
    async def test_concurrent_runs_never_record_into_each_other(self):
        """Requests share providers and nodes; only the context keeps them apart."""

        async def one_run(name: str) -> RunTrace:
            with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
                for _ in range(5):
                    await run_step(name, lambda: calling_the_model(name, (1, 1)))
                    await asyncio.sleep(0)
            return trace

        first, second = await asyncio.gather(one_run("a"), one_run("b"))

        assert {step.node for step in first.steps} == {"a"}
        assert {step.node for step in second.steps} == {"b"}
        assert {call.name for call in first.model_calls()} == {"a"}
        assert len(second.model_calls()) == 5


def describe(node: str, update: dict[str, object] | None) -> dict[str, object] | None:
    return {"attempt": 1} if node == "drafting" else None


class TestRunRecord:
    async def record(self, error: BaseException | None = None):
        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            await run_step("drafting", lambda: calling_the_model("conversation.reply", (12, 30)))
            await run_step("checking", lambda: calling_the_model("conversation.verify"))
            await run_step("drafting", lambda: returning({"reply": DRAFT}))

        return build_run_record(
            trace,
            kind=RunKind.CONVERSATION_TURN,
            user_id=uuid.uuid4(),
            conversation_id=None,
            request_id="req-1",
            result={"reply": DRAFT, "generation_attempts": 2, "grounded": True},
            error=error,
            describe=describe,
        )

    async def test_what_the_steps_returned_is_never_stored(self):
        run = await self.record()

        assert "marathon" not in json.dumps(run.steps)

    async def test_step_detail_comes_only_from_the_describer(self):
        run = await self.record()

        assert [step["detail"] for step in run.steps] == [{"attempt": 1}, {}, {"attempt": 1}]

    async def test_totals_are_summed_from_the_calls_that_reported_usage(self):
        run = await self.record()

        assert run.model_calls == 2
        assert (run.input_tokens, run.output_tokens) == (12, 30)

    async def test_no_reported_usage_is_null_rather_than_zero(self):
        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            await run_step("checking", lambda: calling_the_model("conversation.verify"))

        run = build_run_record(
            trace,
            kind=RunKind.CONVERSATION_TURN,
            user_id=uuid.uuid4(),
            conversation_id=None,
            request_id=None,
            result={},
            error=None,
            describe=describe,
        )

        assert run.input_tokens is None

    async def test_a_failed_run_keeps_the_type_but_not_the_message(self):
        run = await self.record(error=RuntimeError("password=hunter2"))

        assert run.status is RunStatus.FAILED
        assert run.error_type == "RuntimeError"
        assert "hunter2" not in json.dumps(run.steps)


class TestLogs:
    async def test_log_lines_inside_a_run_carry_its_id(self):
        record = logging.LogRecord("app", logging.INFO, __file__, 1, "something_happened", (), None)

        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            inside = json.loads(JsonFormatter().format(record))
        outside = json.loads(JsonFormatter().format(record))

        assert inside["run_id"] == str(trace.run_id)
        assert "run_id" not in outside
