"""Analysis runs end to end: real HTTP, real database, a scripted model.

Each generated analysis is recorded as a run of kind ``analysis``. The trace
read back through ``/api/v1/runs`` is cross-checked against the ``analyses``
row and the API response.
"""

import json
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.analysis.claims import AnalysisResult, ClaimType
from app.domain.subscription.plans import Feature, usage_period
from app.infrastructure.database.models import AgentRun, Analysis, RunKind, RunStatus, UsageCounter
from app.infrastructure.database.repositories.analysis import AnalysisRepository
from app.infrastructure.llm.prompt import Prompt
from app.main import create_app
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider
from tests.integration.test_analysis import authenticate, claim, give_profile, good_analysis

pytestmark = pytest.mark.integration

HALLUCINATED = AnalysisResult(
    claims=[claim("Ran a marathon in Berlin last spring", evidence_ids=["goal:99"])]
)


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(analyses=[good_analysis()])


async def generate(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.post("/api/v1/analysis", headers=headers)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def read_trace(client: AsyncClient, headers: dict[str, str], run_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/v1/runs/{run_id}/trace", headers=headers)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def nodes(trace: dict[str, Any]) -> list[str]:
    return [step["node"] for step in trace["steps"]]


def last_detail(trace: dict[str, Any], node: str) -> dict[str, Any]:
    details: list[dict[str, Any]] = [s["detail"] for s in trace["steps"] if s["node"] == node]
    assert details, f"no {node} step"
    return details[-1]


def response_claims(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for section in data["sections"].values() for item in section]


async def stored_analysis(db_session: AsyncSession) -> Analysis:
    db_session.expire_all()
    return (await db_session.scalars(select(Analysis))).one()


def break_model(provider: FakeLLMProvider, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure the nodes do not absorb, unlike a provider error."""

    async def broken(prompt: Prompt, schema: type[BaseModel]) -> BaseModel:
        raise ConnectionError("connection to 10.0.0.5 lost")

    monkeypatch.setattr(provider, "generate_structured", broken)


class TestTheRun:
    async def test_an_analysis_names_the_run_that_made_it(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)
        body = await generate(api_client, headers)

        response = await api_client.get(f"/api/v1/runs/{body['data']['run_id']}", headers=headers)

        run = response.json()["data"]
        assert run["kind"] == RunKind.ANALYSIS.value
        assert run["status"] == RunStatus.COMPLETED.value
        assert run["conversation_id"] is None
        assert run["model_calls"] == 1
        assert run["request_id"] == body["meta"]["request_id"]

    async def test_an_analysis_read_later_carries_no_run(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)
        await generate(api_client, headers)

        latest = await api_client.get("/api/v1/analysis/latest", headers=headers)

        assert latest.json()["data"]["run_id"] is None

    async def test_a_refusal_for_lack_of_evidence_runs_no_graph_and_records_no_run(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.post("/api/v1/analysis", headers=headers)

        assert response.status_code == 409
        assert await db_session.scalar(select(func.count()).select_from(AgentRun)) == 0

    async def test_the_trace_lists_each_node_with_its_model_calls(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)
        body = await generate(api_client, headers)

        trace = await read_trace(api_client, headers, body["data"]["run_id"])

        assert [(s["node"], [c["name"] for c in s["model_calls"]]) for s in trace["steps"]] == [
            ("build_evidence", []),
            ("generate_analysis", ["analysis.initial"]),
            ("verify_grounding", []),
        ]


class TestCrossCheck:
    async def test_the_trace_agrees_with_the_row_and_the_response(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)
        body = await generate(api_client, headers)
        data = body["data"]

        trace = await read_trace(api_client, headers, data["run_id"])

        analysis = await stored_analysis(db_session)
        verified = last_detail(trace, "verify_grounding")
        claims = response_claims(data)
        assert trace["run"]["generation_attempts"] == analysis.generation_attempts == 1
        assert trace["run"]["degraded"] is analysis.degraded is data["degraded"] is False
        assert verified["claims_kept"] == len(analysis.claims) == len(claims)
        assert verified["claim_types"] == dict(Counter(item["claim_type"] for item in claims))
        assert sum(verified["rejected"].values()) == analysis.rejected_claim_count
        assert verified["overall_confidence"] == round(analysis.overall_confidence, 4)
        assert verified["overall_confidence"] == data["overall_confidence"]
        assert last_detail(trace, "build_evidence") == {"evidence_items": len(data["evidence"])}

    async def test_a_retry_is_traced_and_counted_like_the_row(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        llm_provider._analyses = [HALLUCINATED, good_analysis()]
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)
        body = await generate(api_client, headers)

        trace = await read_trace(api_client, headers, body["data"]["run_id"])

        assert nodes(trace) == [
            "build_evidence",
            "generate_analysis",
            "verify_grounding",
            "generate_analysis",
            "verify_grounding",
        ]
        first_verify = next(s for s in trace["steps"] if s["node"] == "verify_grounding")
        assert first_verify["detail"]["rejected"] == {"unknown_evidence": 1}
        analysis = await stored_analysis(db_session)
        assert trace["run"]["generation_attempts"] == analysis.generation_attempts == 2
        assert trace["run"]["model_calls"] == 2
        assert last_detail(trace, "generate_analysis")["attempt"] == 2

    async def test_an_exhausted_budget_is_a_degraded_run_and_a_degraded_analysis(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        llm_provider._analyses = [HALLUCINATED]
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)
        body = await generate(api_client, headers)

        trace = await read_trace(api_client, headers, body["data"]["run_id"])

        analysis = await stored_analysis(db_session)
        assert analysis.degraded is True
        assert trace["run"]["degraded"] is True
        assert last_detail(trace, "verify_grounding")["degraded"] is True
        assert last_detail(trace, "verify_grounding")["claims_kept"] == 0
        assert analysis.rejected_claim_count == sum(
            last_detail(trace, "verify_grounding")["rejected"].values()
        )


class TestFailedRuns:
    async def test_a_run_that_raises_is_stored_failed_without_an_analysis(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)
        break_model(llm_provider, monkeypatch)

        with pytest.raises(ConnectionError):
            await api_client.post("/api/v1/analysis", headers=headers)

        run = (await db_session.scalars(select(AgentRun))).one()
        assert run.kind is RunKind.ANALYSIS
        assert run.status is RunStatus.FAILED
        assert run.failed_node == "generate_analysis"
        assert run.error_type == "ConnectionError"
        assert await db_session.scalar(select(func.count()).select_from(Analysis)) == 0

        trace = await read_trace(api_client, headers, str(run.id))
        assert [(s["node"], s["status"]) for s in trace["steps"]] == [
            ("build_evidence", "ok"),
            ("generate_analysis", "failed"),
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

    async def test_a_failed_generation_reports_its_node_and_error(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)
        break_model(llm_provider, monkeypatch)

        with pytest.raises(ConnectionError):
            await api_client.post("/api/v1/analysis", headers=headers)

        run_id = await failed_run_id(db_session)
        run = await read_run(api_client, headers, run_id)
        trace = await read_trace(api_client, headers, run_id)
        assert run["kind"] == "analysis"
        assert run["status"] == "failed"
        assert run["failed_node"] == "generate_analysis"
        assert run["error_type"] == "ConnectionError"
        assert trace["run"] == run
        assert trace["steps"][-1]["node"] == run["failed_node"]
        assert trace["steps"][-1]["error_type"] == run["error_type"]

    async def test_a_failure_on_the_retry_keeps_the_steps_before_it(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The first attempt is rejected, the second raises: the trace shows both."""
        calls = 0

        async def fails_second_time(prompt: Prompt, schema: type[BaseModel]) -> BaseModel:
            nonlocal calls
            calls += 1
            if calls == 1:
                return HALLUCINATED
            raise TimeoutError("second attempt hung")

        monkeypatch.setattr(llm_provider, "generate_structured", fails_second_time)
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)

        with pytest.raises(TimeoutError):
            await api_client.post("/api/v1/analysis", headers=headers)

        trace = await read_trace(api_client, headers, await failed_run_id(db_session))
        assert trace["run"]["failed_node"] == "generate_analysis"
        assert trace["run"]["error_type"] == "TimeoutError"
        assert trace["run"]["model_calls"] == 2
        assert [(s["node"], s["status"]) for s in trace["steps"]] == [
            ("build_evidence", "ok"),
            ("generate_analysis", "ok"),
            ("verify_grounding", "ok"),
            ("generate_analysis", "failed"),
        ]
        assert trace["steps"][2]["detail"]["rejected"] == {"unknown_evidence": 1}
        assert await db_session.scalar(select(func.count()).select_from(Analysis)) == 0

    async def test_a_failure_after_the_graph_has_no_failed_node(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Every node succeeded; storing the analysis failed. The run says exactly that."""

        def store_fails(self: Any, analysis: Analysis) -> None:
            raise RuntimeError("could not store")

        monkeypatch.setattr(AnalysisRepository, "add", store_fails)
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)

        with pytest.raises(RuntimeError):
            await api_client.post("/api/v1/analysis", headers=headers)

        trace = await read_trace(api_client, headers, await failed_run_id(db_session))
        assert trace["run"]["status"] == "failed"
        assert trace["run"]["failed_node"] is None
        assert trace["run"]["error_type"] == "RuntimeError"
        assert [(s["node"], s["status"]) for s in trace["steps"]] == [
            ("build_evidence", "ok"),
            ("generate_analysis", "ok"),
            ("verify_grounding", "ok"),
        ]
        assert "could not store" not in json.dumps(trace)

    async def test_a_successful_analysis_reports_no_failure(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)
        run_id = (await generate(api_client, headers))["data"]["run_id"]

        run = await read_run(api_client, headers, run_id)

        assert (run["status"], run["failed_node"], run["error_type"]) == ("completed", None, None)
        trace = await read_trace(api_client, headers, run_id)
        assert all(s["status"] == "ok" and s["error_type"] is None for s in trace["steps"])


class TestPrivacyAndOwnership:
    async def test_the_trace_holds_no_claim_or_profile_text(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)
        body = await generate(api_client, headers)

        text = json.dumps(await read_trace(api_client, headers, body["data"]["run_id"]))

        for written in (
            *(item.content for item in good_analysis().claims),
            "Move into AI engineering",
            "Only evenings free",
            "direct",
        ):
            assert written not in text
        assert ClaimType.FACT.value in text, "claim types are a fixed vocabulary and are kept"

    async def test_another_users_analysis_run_is_not_found(self, api_client: AsyncClient):
        owner = await authenticate(api_client, "grace@example.com")
        await give_profile(api_client, owner)
        run_id = (await generate(api_client, owner))["data"]["run_id"]
        intruder = await authenticate(api_client, "ada@example.com")

        for path in (run_id, f"{run_id}/trace"):
            response = await api_client.get(f"/api/v1/runs/{path}", headers=intruder)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "RUN_NOT_FOUND"


@pytest.fixture
async def metered_client(
    database_settings: Settings, clean_database: None, llm_provider: FakeLLMProvider
) -> AsyncIterator[AsyncClient]:
    application: FastAPI = create_app(
        database_settings.model_copy(update={"usage_limits_enabled": True}),
        llm_provider=llm_provider,
        embedding_provider=FakeEmbeddingProvider(),
    )
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    await application.state.database_engine.dispose()


class TestRecordingAFailureKeepsTheRefund:
    async def test_a_failed_analysis_is_traced_and_still_refunded(
        self,
        metered_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The failed run is committed inside the request; the refund must survive it."""
        headers = await authenticate(metered_client, "ada@example.com")
        await give_profile(metered_client, headers)
        break_model(llm_provider, monkeypatch)

        with pytest.raises(ConnectionError):
            await metered_client.post("/api/v1/analysis", headers=headers)

        run = (await db_session.scalars(select(AgentRun))).one()
        assert run.status is RunStatus.FAILED
        owner = run.user_id
        db_session.expire_all()
        used = await db_session.scalar(
            select(UsageCounter.used).where(
                UsageCounter.user_id == owner,
                UsageCounter.feature == Feature.ANALYSIS,
                UsageCounter.period_start == usage_period(datetime.now(tz=UTC)).starts_on,
            )
        )
        assert (used or 0) == 0
