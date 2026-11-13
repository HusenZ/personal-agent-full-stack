"""Onboarding runs end to end: real HTTP, real database, a scripted model.

Each interview turn that runs the graph is recorded as a run of kind
``onboarding_turn``. The trace read back through ``/api/v1/runs`` is
cross-checked against the onboarding session row and the API response.
"""

import json
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.profile.service import ProfileService
from app.graph.onboarding.nodes import assess_context
from app.infrastructure.database.models import (
    AgentRun,
    OnboardingSession,
    OnboardingStatus,
    RunKind,
    RunStatus,
)
from app.infrastructure.llm.exceptions import LLMUnavailableError
from app.infrastructure.llm.prompt import Prompt
from app.infrastructure.llm.prompts.onboarding import OPENING_QUESTION
from tests.fakes import FakeLLMProvider
from tests.integration.test_onboarding import authenticate, rich_extraction

pytestmark = pytest.mark.integration

ANSWER = "I want to leave my job at the bank and move into AI engineering."
QUESTION = "What stands between you and that change today?"


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(
        extractions=[rich_extraction() for _ in range(15)],
        questions=[QUESTION for _ in range(15)],
    )


async def start(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.post("/api/v1/onboarding/start", headers=headers)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


async def answer(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/onboarding/message", headers=headers, json={"answer": ANSWER}
    )
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


async def read_trace(client: AsyncClient, headers: dict[str, str], run_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/v1/runs/{run_id}/trace", headers=headers)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def detail(trace: dict[str, Any], node: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = [s["detail"] for s in trace["steps"] if s["node"] == node]
    assert len(matches) == 1, f"expected exactly one {node} step"
    return matches[0]


async def count_runs(db_session: AsyncSession) -> int:
    return await db_session.scalar(select(func.count()).select_from(AgentRun)) or 0


def break_model(provider: FakeLLMProvider, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure the nodes do not absorb, unlike a provider error."""

    async def broken(prompt: Prompt, schema: type[BaseModel]) -> BaseModel:
        raise ConnectionError("connection to 10.0.0.5 lost")

    monkeypatch.setattr(provider, "generate_structured", broken)


class TestTheRun:
    async def test_the_opening_turn_names_its_run(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        turn = await start(api_client, headers)

        run = (await api_client.get(f"/api/v1/runs/{turn['run_id']}", headers=headers)).json()[
            "data"
        ]

        assert run["kind"] == RunKind.ONBOARDING_TURN.value
        assert run["status"] == RunStatus.COMPLETED.value
        assert run["conversation_id"] is None
        assert run["model_calls"] == 0, "the opening question is fixed"
        assert run["degraded"] is False

    async def test_every_graph_turn_is_one_run_and_reads_are_none(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        opened = await start(api_client, headers)
        answered = await answer(api_client, headers)
        resumed = await start(api_client, headers)
        status = (await api_client.get("/api/v1/onboarding/status", headers=headers)).json()

        assert opened["run_id"] and answered["run_id"]
        assert opened["run_id"] != answered["run_id"]
        assert resumed["run_id"] is None, "resuming runs no graph"
        assert status["data"]["run_id"] is None
        assert await count_runs(db_session) == 2

    async def test_an_answer_turn_traces_each_node_with_its_model_calls(
        self, api_client: AsyncClient
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await start(api_client, headers)
        turn = await answer(api_client, headers)

        trace = await read_trace(api_client, headers, turn["run_id"])

        assert [(s["node"], [c["name"] for c in s["model_calls"]]) for s in trace["steps"]] == [
            ("extract_information", ["onboarding.extraction"]),
            ("assess_context", []),
            ("generate_question", ["onboarding.question"]),
        ]
        assert trace["run"]["model_calls"] == 2


class TestCrossCheck:
    async def test_the_assessment_agrees_with_the_session_and_response(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await start(api_client, headers)
        turn = await answer(api_client, headers)

        trace = await read_trace(api_client, headers, turn["run_id"])

        session = (await db_session.scalars(select(OnboardingSession))).one()
        assessed = detail(trace, "assess_context")
        assert assessed["overall_confidence"] == round(session.overall_confidence, 4)
        assert assessed["overall_confidence"] == turn["overall_confidence"]
        assert assessed["coverage"] == turn["coverage"]
        assert assessed["completion_reason"] == session.state["completion_reason"]
        assert assessed["is_complete"] is turn["is_complete"]

        asked = detail(trace, "generate_question")
        assert asked["questions_asked"] == session.questions_asked == turn["questions_asked"]
        assert asked["dimension"] == turn["question"]["dimension"]
        assert detail(trace, "extract_information") == {
            "answer_usable": True,
            "extraction_failed": False,
        }

    async def test_the_completing_turn_builds_the_profile_it_stored(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        turn = await start(api_client, headers)
        for _ in range(20):
            if turn["is_complete"]:
                break
            turn = await answer(api_client, headers)
        assert turn["is_complete"]

        trace = await read_trace(api_client, headers, turn["run_id"])
        profile = (await api_client.get("/api/v1/profile", headers=headers)).json()["data"]

        assert [s["node"] for s in trace["steps"]] == [
            "extract_information",
            "assess_context",
            "build_profile",
        ]
        assert detail(trace, "assess_context")["is_complete"] is True
        built = detail(trace, "build_profile")
        assert built["goals"] == len(profile["goals"])
        assert built["constraints"] == len(profile["constraints"])
        assert built["preferences"] == len(profile["preferences"])
        session = (await db_session.scalars(select(OnboardingSession))).one()
        assert session.status is OnboardingStatus.COMPLETED


class TestDegradedAndFailedTurns:
    async def test_a_provider_outage_is_a_degraded_run(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await start(api_client, headers)
        llm_provider._failure = LLMUnavailableError("down")

        turn = await answer(api_client, headers)

        trace = await read_trace(api_client, headers, turn["run_id"])
        assert trace["run"]["status"] == "completed"
        assert trace["run"]["degraded"] is True
        assert detail(trace, "extract_information")["extraction_failed"] is True
        failed_calls = [
            (s["node"], c["error_type"])
            for s in trace["steps"]
            for c in s["model_calls"]
            if c["status"] == "failed"
        ]
        assert failed_calls == [
            ("extract_information", "LLMUnavailableError"),
            ("generate_question", "LLMUnavailableError"),
        ]

    async def test_a_turn_that_raises_leaves_a_failed_run_and_no_progress(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await start(api_client, headers)
        break_model(llm_provider, monkeypatch)

        with pytest.raises(ConnectionError):
            await api_client.post(
                "/api/v1/onboarding/message", headers=headers, json={"answer": ANSWER}
            )

        failed = (
            await db_session.scalars(select(AgentRun).where(AgentRun.status == RunStatus.FAILED))
        ).one()
        assert failed.kind is RunKind.ONBOARDING_TURN
        assert failed.failed_node == "extract_information"
        assert failed.error_type == "ConnectionError"
        session = (await db_session.scalars(select(OnboardingSession))).one()
        assert session.questions_asked == 1, "the failed turn's state was rolled back"

        trace = await read_trace(api_client, headers, str(failed.id))
        assert [(s["node"], s["status"]) for s in trace["steps"]] == [
            ("extract_information", "failed")
        ]
        assert "10.0.0.5" not in json.dumps(trace)


async def read_run(client: AsyncClient, headers: dict[str, str], run_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/v1/runs/{run_id}", headers=headers)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


async def failed_run_id(db_session: AsyncSession) -> str:
    """The id only; everything asserted about the run is read through the API."""
    db_session.expire_all()
    run_id = (
        await db_session.scalars(select(AgentRun.id).where(AgentRun.status == RunStatus.FAILED))
    ).one()
    return str(run_id)


class TestFailedRunsThroughTheEndpoint:
    """Failures read back through GET /runs/{id} and /runs/{id}/trace, not the table."""

    async def test_a_failed_answer_turn_reports_its_node_and_error(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await start(api_client, headers)
        break_model(llm_provider, monkeypatch)

        with pytest.raises(ConnectionError):
            await api_client.post(
                "/api/v1/onboarding/message", headers=headers, json={"answer": ANSWER}
            )

        run_id = await failed_run_id(db_session)
        run = await read_run(api_client, headers, run_id)
        trace = await read_trace(api_client, headers, run_id)
        assert run["kind"] == "onboarding_turn"
        assert run["status"] == "failed"
        assert run["failed_node"] == "extract_information"
        assert run["error_type"] == "ConnectionError"
        assert trace["run"] == run
        assert trace["steps"][-1]["node"] == run["failed_node"]
        assert trace["steps"][-1]["error_type"] == run["error_type"]

    async def test_a_failed_opening_turn_is_recorded_and_starts_no_interview(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The new interview row is rolled back; the failed run survives it."""

        def broken(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("scoring failed")

        monkeypatch.setattr(assess_context, "assess_coverage", broken)
        headers = await authenticate(api_client, "ada@example.com")

        with pytest.raises(RuntimeError):
            await api_client.post("/api/v1/onboarding/start", headers=headers)

        trace = await read_trace(api_client, headers, await failed_run_id(db_session))
        assert trace["run"]["failed_node"] == "assess_context"
        assert trace["run"]["error_type"] == "RuntimeError"
        assert [(s["node"], s["status"]) for s in trace["steps"]] == [
            ("extract_information", "ok"),
            ("assess_context", "failed"),
        ]
        assert await db_session.scalar(select(func.count()).select_from(OnboardingSession)) == 0
        assert "scoring failed" not in json.dumps(trace)

    async def test_a_failure_after_the_graph_has_no_failed_node(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Every node succeeded; the profile write failed. The run says exactly that."""
        headers = await authenticate(api_client, "ada@example.com")
        turn = await start(api_client, headers)

        async def write_fails(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("profile write failed")

        monkeypatch.setattr(ProfileService, "apply_update", write_fails)
        with pytest.raises(RuntimeError):
            for _ in range(20):
                turn = await answer(api_client, headers)
                assert not turn["is_complete"], "completion should have failed"

        trace = await read_trace(api_client, headers, await failed_run_id(db_session))
        assert trace["run"]["status"] == "failed"
        assert trace["run"]["failed_node"] is None
        assert trace["run"]["error_type"] == "RuntimeError"
        assert [(s["node"], s["status"]) for s in trace["steps"]] == [
            ("extract_information", "ok"),
            ("assess_context", "ok"),
            ("build_profile", "ok"),
        ]
        session = (await db_session.scalars(select(OnboardingSession))).one()
        assert session.status is OnboardingStatus.IN_PROGRESS, "the completion rolled back"

    async def test_a_successful_turn_reports_no_failure(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await start(api_client, headers)
        turn = await answer(api_client, headers)

        run = await read_run(api_client, headers, turn["run_id"])

        assert (run["status"], run["failed_node"], run["error_type"]) == ("completed", None, None)
        trace = await read_trace(api_client, headers, turn["run_id"])
        assert all(s["status"] == "ok" and s["error_type"] is None for s in trace["steps"])


class TestPrivacyAndOwnership:
    async def test_the_trace_holds_no_answer_or_question_text(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await start(api_client, headers)
        turn = await answer(api_client, headers)

        text = json.dumps(await read_trace(api_client, headers, turn["run_id"]))

        for written in (ANSWER, QUESTION, OPENING_QUESTION, "AI engineering", "Flutter"):
            assert written not in text

    async def test_another_users_onboarding_run_is_not_found(self, api_client: AsyncClient):
        owner = await authenticate(api_client, "grace@example.com")
        run_id = (await start(api_client, owner))["run_id"]
        intruder = await authenticate(api_client, "ada@example.com")

        for path in (run_id, f"{run_id}/trace"):
            response = await api_client.get(f"/api/v1/runs/{path}", headers=intruder)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "RUN_NOT_FOUND"
