"""Failure diagnostics through the API, and run health across users, on real PostgreSQL."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.runs.health import RunHealthService
from app.infrastructure.database.models import AgentRun, RunKind, RunStatus, User
from app.infrastructure.database.repositories.analysis import AnalysisRepository
from app.infrastructure.database.repositories.run import RunRepository
from app.infrastructure.llm.exceptions import LLMUnavailableError
from app.infrastructure.llm.prompt import Prompt
from tests.fakes import FakeLLMProvider
from tests.integration.test_analysis import give_profile, good_analysis
from tests.integration.test_chat import authenticate
from tests.integration.test_plans import WEEK, with_profile

pytestmark = pytest.mark.integration


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(weeks=[WEEK], analyses=[good_analysis()])


async def only_failed_run_id(db_session: AsyncSession) -> str:
    db_session.expire_all()
    run_id = (
        await db_session.scalars(select(AgentRun.id).where(AgentRun.status == RunStatus.FAILED))
    ).one()
    return str(run_id)


async def read_trace(client: AsyncClient, headers: dict[str, str], run_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/v1/runs/{run_id}/trace", headers=headers)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


class TestDiagnosticsThroughTheApi:
    async def test_a_wrapped_provider_failure_keeps_its_root_cause(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers = await with_profile(api_client)
        llm_provider._failure = LLMUnavailableError("provider at 10.0.0.5 timed out")

        assert (await api_client.post("/api/v1/plans/week", headers=headers)).status_code == 503

        trace = await read_trace(api_client, headers, await only_failed_run_id(db_session))
        run = trace["run"]
        assert run["failed_node"] == "propose_week"
        assert run["error_type"] == "PlanUnavailableError"
        assert run["error_chain"] == ["LLMUnavailableError"]
        assert run["error_location"].startswith("app/application/planning/service.py:")
        assert run["error_location"].endswith(" in _propose")
        failed_step = trace["steps"][-1]
        assert failed_step["error_chain"] == run["error_chain"]
        assert failed_step["error_location"] == run["error_location"]
        assert "10.0.0.5" not in json.dumps(trace)

    async def test_a_failure_in_a_node_points_at_the_node_not_the_tracing_wrapper(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        async def broken(prompt: Prompt, schema: type[BaseModel]) -> BaseModel:
            raise ConnectionError("socket closed")

        monkeypatch.setattr(llm_provider, "generate_structured", broken)
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)

        with pytest.raises(ConnectionError):
            await api_client.post("/api/v1/analysis", headers=headers)

        run = (await read_trace(api_client, headers, await only_failed_run_id(db_session)))["run"]
        assert run["failed_node"] == "generate_analysis"
        assert run["error_location"].startswith("app/graph/analysis/nodes/generate_analysis.py:")
        assert run["error_location"].endswith(" in generate_analysis")
        assert run["error_chain"] == []

    async def test_a_failure_outside_every_node_still_says_where(
        self, api_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ):
        def store_fails(self: Any, analysis: Any) -> None:
            raise RuntimeError("could not store")

        monkeypatch.setattr(AnalysisRepository, "add", store_fails)
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)

        with pytest.raises(RuntimeError):
            await api_client.post("/api/v1/analysis", headers=headers)

        run = (await read_trace(api_client, headers, await only_failed_run_id(db_session)))["run"]
        assert run["failed_node"] is None
        assert run["error_location"].startswith("app/application/analysis/service.py:")
        assert run["error_location"].endswith(" in _run")

    async def test_a_successful_run_has_no_diagnostics(self, api_client: AsyncClient):
        headers = await with_profile(api_client)
        run_id = (await api_client.post("/api/v1/plans/week", headers=headers)).json()["data"][
            "run_id"
        ]

        trace = await read_trace(api_client, headers, run_id)

        assert trace["run"]["error_chain"] is None
        assert trace["run"]["error_location"] is None
        assert all(s["error_chain"] == [] and s["error_location"] is None for s in trace["steps"])

    async def test_a_trace_stored_before_diagnostics_existed_still_reads(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        owner = (await db_session.scalars(select(User.id))).one()
        legacy = AgentRun(
            id=uuid.uuid4(),
            user_id=owner,
            kind=RunKind.CONVERSATION_TURN,
            status=RunStatus.FAILED,
            started_at=datetime.now(tz=UTC),
            duration_ms=3.0,
            model_calls=0,
            retrieval_attempts=0,
            generation_attempts=0,
            error_type="ConnectionError",
            failed_node="retrieve_context",
            # The step shape before error_chain and error_location were added.
            steps=[
                {
                    "node": "retrieve_context",
                    "offset_ms": 0.1,
                    "duration_ms": 2.9,
                    "status": "failed",
                    "error_type": "ConnectionError",
                    "detail": {},
                    "model_calls": [],
                }
            ],
        )
        db_session.add(legacy)
        await db_session.commit()

        trace = await read_trace(api_client, headers, str(legacy.id))

        assert trace["run"]["error_chain"] is None
        assert trace["steps"][0]["error_chain"] == []
        assert trace["steps"][0]["error_location"] is None


NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def step(
    node: str,
    duration: float,
    status: str = "ok",
    error: str | None = None,
    calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "node": node,
        "offset_ms": 0.0,
        "duration_ms": duration,
        "status": status,
        "error_type": error,
        "error_chain": [],
        "error_location": None,
        "detail": {},
        "model_calls": calls or [],
    }


def call(name: str, status: str = "ok", error: str | None = None) -> dict[str, Any]:
    return {
        "kind": "structured",
        "name": name,
        "version": "1",
        "duration_ms": 1.0,
        "status": status,
        "error_type": error,
        "input_tokens": None,
        "output_tokens": None,
    }


def run(
    owner: uuid.UUID,
    kind: RunKind,
    steps: list[dict[str, Any]],
    *,
    age: timedelta = timedelta(hours=1),
    status: RunStatus = RunStatus.COMPLETED,
    degraded: bool = False,
    failed_node: str | None = None,
    error_type: str | None = None,
    error_chain: list[str] | None = None,
    error_location: str | None = None,
) -> AgentRun:
    return AgentRun(
        id=uuid.uuid4(),
        user_id=owner,
        kind=kind,
        status=status,
        started_at=NOW - age,
        duration_ms=sum(item["duration_ms"] for item in steps),
        model_calls=sum(len(item["model_calls"]) for item in steps),
        retrieval_attempts=0,
        generation_attempts=0,
        degraded=degraded,
        failed_node=failed_node,
        error_type=error_type,
        error_chain=error_chain,
        error_location=error_location,
        steps=steps,
    )


@pytest.fixture
async def seeded(api_client: AsyncClient, db_session: AsyncSession) -> None:
    """Runs across two users and three kinds, one outside the window."""
    await authenticate(api_client, "ada@example.com")
    await authenticate(api_client, "grace@example.com")
    ada, grace = (await db_session.scalars(select(User.id).order_by(User.email))).all()
    location = "app/graph/analysis/nodes/generate_analysis.py:27 in generate_analysis"

    db_session.add_all(
        [
            run(
                ada,
                RunKind.ANALYSIS,
                [
                    step("build_evidence", 1.0),
                    step("generate_analysis", 10.0, calls=[call("analysis.initial")]),
                    step("verify_grounding", 2.0),
                ],
            ),
            run(
                grace,
                RunKind.ANALYSIS,
                [
                    step("build_evidence", 3.0),
                    step(
                        "generate_analysis",
                        30.0,
                        "failed",
                        "PlanUnavailableError",
                        calls=[call("analysis.initial", "failed", "LLMUnavailableError")],
                    ),
                ],
                status=RunStatus.FAILED,
                failed_node="generate_analysis",
                error_type="PlanUnavailableError",
                error_chain=["LLMUnavailableError"],
                error_location=location,
            ),
            # Survived a model failure: degraded, not failed.
            run(
                ada,
                RunKind.ONBOARDING_TURN,
                [
                    step(
                        "extract_information",
                        5.0,
                        calls=[call("onboarding.extraction", "failed", "LLMTimeoutError")],
                    ),
                    step("assess_context", 1.0),
                    step("generate_question", 4.0, "cancelled"),
                ],
                degraded=True,
            ),
            # Every node succeeded; storing the result failed.
            run(
                grace,
                RunKind.WEEK_PLAN,
                [step("collect_evidence", 1.0), step("propose_week", 2.0)],
                status=RunStatus.FAILED,
                error_type="IntegrityError",
                error_chain=["UniqueViolationError"],
                error_location="app/application/planning/service.py:170 in _generate",
            ),
            # Outside a 24-hour window.
            run(
                ada,
                RunKind.ANALYSIS,
                [step("build_evidence", 100.0)],
                age=timedelta(days=3),
                status=RunStatus.FAILED,
                failed_node="build_evidence",
                error_type="OldError",
            ),
        ]
    )
    await db_session.commit()


def service(db_session: AsyncSession) -> RunHealthService:
    return RunHealthService(RunRepository(db_session), clock=lambda: NOW)


class TestRunHealth:
    async def test_runs_are_summarised_by_kind_within_the_window(
        self, seeded: None, db_session: AsyncSession
    ):
        report = await service(db_session).report(window=timedelta(hours=24))

        by_kind = {item.kind: item for item in report.kinds}
        assert set(by_kind) == {RunKind.ANALYSIS, RunKind.ONBOARDING_TURN, RunKind.WEEK_PLAN}
        analysis = by_kind[RunKind.ANALYSIS]
        assert (analysis.runs, analysis.failed, analysis.degraded) == (2, 1, 0)
        assert analysis.model_calls == 2
        assert analysis.p50_ms == pytest.approx(23.0)
        assert by_kind[RunKind.ONBOARDING_TURN].degraded == 1

    async def test_each_node_is_counted_with_its_failures_and_timings(
        self, seeded: None, db_session: AsyncSession
    ):
        report = await service(db_session).report(window=timedelta(hours=24))

        nodes = {(item.kind, item.node): item for item in report.nodes}
        generate = nodes[(RunKind.ANALYSIS, "generate_analysis")]
        assert (generate.executions, generate.failed, generate.cancelled) == (2, 1, 0)
        assert generate.p50_ms == pytest.approx(20.0)
        assert generate.max_ms == pytest.approx(30.0)
        assert nodes[(RunKind.ANALYSIS, "build_evidence")].executions == 2, "old run excluded"
        question = nodes[(RunKind.ONBOARDING_TURN, "generate_question")]
        assert (question.failed, question.cancelled) == (0, 1)

    async def test_failed_runs_are_grouped_by_where_and_why(
        self, seeded: None, db_session: AsyncSession
    ):
        report = await service(db_session).report(window=timedelta(hours=24))

        groups = {(item.kind, item.failed_node): item for item in report.failures}
        assert set(groups) == {(RunKind.ANALYSIS, "generate_analysis"), (RunKind.WEEK_PLAN, None)}
        in_node = groups[(RunKind.ANALYSIS, "generate_analysis")]
        assert (in_node.error_type, in_node.root_cause) == (
            "PlanUnavailableError",
            "LLMUnavailableError",
        )
        assert in_node.error_location is not None
        outside = groups[(RunKind.WEEK_PLAN, None)]
        assert outside.root_cause == "UniqueViolationError"
        assert outside.error_location == "app/application/planning/service.py:170 in _generate"

    async def test_model_call_failures_include_ones_the_run_survived(
        self, seeded: None, db_session: AsyncSession
    ):
        report = await service(db_session).report(window=timedelta(hours=24))

        failures = {
            (item.kind, item.node, item.call, item.error_type)
            for item in report.model_call_failures
        }
        assert failures == {
            (RunKind.ANALYSIS, "generate_analysis", "analysis.initial", "LLMUnavailableError"),
            (
                RunKind.ONBOARDING_TURN,
                "extract_information",
                "onboarding.extraction",
                "LLMTimeoutError",
            ),
        }

    async def test_a_report_can_be_narrowed_to_one_kind_and_widened_in_time(
        self, seeded: None, db_session: AsyncSession
    ):
        report = await service(db_session).report(window=timedelta(days=7), kind=RunKind.ANALYSIS)

        assert [item.kind for item in report.kinds] == [RunKind.ANALYSIS]
        assert report.kinds[0].runs == 3
        assert {item.kind for item in report.nodes} == {RunKind.ANALYSIS}
        assert {item.error_type for item in report.failures} == {"PlanUnavailableError", "OldError"}

    async def test_failure_groups_are_limited_most_frequent_first(
        self, seeded: None, db_session: AsyncSession
    ):
        report = await service(db_session).report(window=timedelta(days=7), limit=1)

        assert len(report.failures) == 1
        assert len(report.model_call_failures) == 1

    async def test_the_report_is_json_ready(self, seeded: None, db_session: AsyncSession):
        report = await service(db_session).report(window=timedelta(hours=24))

        document = json.loads(json.dumps(report.to_dict()))

        assert document["until"] == NOW.isoformat()
        assert {item["kind"] for item in document["kinds"]} == {
            "analysis",
            "onboarding_turn",
            "week_plan",
        }

    @pytest.mark.parametrize(
        ("window", "limit"),
        [(timedelta(0), 20), (timedelta(days=91), 20), (timedelta(hours=1), 0)],
    )
    async def test_an_unbounded_request_is_refused(
        self, db_session: AsyncSession, window: timedelta, limit: int
    ):
        with pytest.raises(ValueError):
            await service(db_session).report(window=window, limit=limit)
