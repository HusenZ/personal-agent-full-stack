"""The week-plan trace, cross-checked against what was actually stored.

Each plan generation is recorded as a run of kind ``week_plan``. These tests
read that trace back through ``/api/v1/runs/{id}/trace`` and compare it with
the ``week_plans`` row (migration 0012) and the API response, so the three
views of one generation (what the trace says happened, what the database
holds, what the client was shown) must agree, block category by category.
"""

import json
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.planning.service import PlanStep
from app.core.config import Settings
from app.domain.planning.week import BlockCategory, ProposedBlock, ProposedWeek
from app.domain.subscription.plans import Feature, usage_period
from app.infrastructure.database.models import AgentRun, RunKind, RunStatus, UsageCounter, WeekPlan
from app.infrastructure.llm.exceptions import LLMUnavailableError
from app.main import create_app
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider
from tests.integration.test_chat import authenticate
from tests.integration.test_plans import WEEK, with_profile

pytestmark = pytest.mark.integration

STEPS = [step.value for step in PlanStep]

# One block for every category, each on its own day and slot so none is
# rejected: the trace, the row and the response must each report all of them.
EVERY_CATEGORY = ProposedWeek(
    summary="A week touching every part of life.",
    blocks=[
        ProposedBlock(
            day=index % 7,
            start=f"{8 + index:02d}:00",
            duration_minutes=30,
            title=f"Block {category.value}",
            category=category,
            reason="Covers one category.",
            evidence_ids=["goal:1"],
        )
        for index, category in enumerate(BlockCategory)
    ],
)


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(weeks=[WEEK])


async def generate(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.post("/api/v1/plans/week", headers=headers)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def read_trace(client: AsyncClient, headers: dict[str, str], run_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/v1/runs/{run_id}/trace", headers=headers)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def step(trace: dict[str, Any], node: PlanStep) -> dict[str, Any]:
    matches: list[dict[str, Any]] = [item for item in trace["steps"] if item["node"] == node.value]
    assert len(matches) == 1, f"expected exactly one {node.value} step"
    return matches[0]


async def only_run(db_session: AsyncSession) -> AgentRun:
    db_session.expire_all()
    runs = (await db_session.scalars(select(AgentRun))).all()
    assert len(runs) == 1
    return runs[0]


async def stored_plan(db_session: AsyncSession) -> WeekPlan:
    db_session.expire_all()
    plans = (await db_session.scalars(select(WeekPlan))).all()
    assert len(plans) == 1
    return plans[0]


class TestTheRun:
    async def test_a_plan_names_the_run_that_made_it(self, api_client: AsyncClient):
        headers = await with_profile(api_client)
        body = await generate(api_client, headers)

        response = await api_client.get(f"/api/v1/runs/{body['data']['run_id']}", headers=headers)

        assert response.status_code == 200
        run = response.json()["data"]
        assert run["kind"] == RunKind.WEEK_PLAN.value
        assert run["status"] == RunStatus.COMPLETED.value
        assert run["conversation_id"] is None
        assert run["model_calls"] == 1
        assert run["generation_attempts"] == 1
        assert run["retrieval_attempts"] == 0
        assert run["failed_node"] is None
        assert run["request_id"] == body["meta"]["request_id"]

    async def test_a_plan_read_later_carries_no_run(self, api_client: AsyncClient):
        headers = await with_profile(api_client)
        await generate(api_client, headers)

        latest = await api_client.get("/api/v1/plans/week/latest", headers=headers)

        assert latest.json()["data"]["run_id"] is None

    async def test_the_run_is_stored_with_the_plan(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await with_profile(api_client)
        body = await generate(api_client, headers)

        run = await only_run(db_session)
        run_id, run_owner = run.id, run.user_id
        plan = await stored_plan(db_session)
        assert str(run_id) == body["data"]["run_id"]
        assert str(plan.id) == body["data"]["id"]
        assert run_owner == plan.user_id


class TestTheTrace:
    async def test_each_step_is_recorded_in_order(self, api_client: AsyncClient):
        headers = await with_profile(api_client)
        body = await generate(api_client, headers)

        trace = await read_trace(api_client, headers, body["data"]["run_id"])

        assert [item["node"] for item in trace["steps"]] == STEPS
        assert all(item["status"] == "ok" for item in trace["steps"])
        offsets = [item["offset_ms"] for item in trace["steps"]]
        assert offsets == sorted(offsets)

    async def test_only_the_proposal_step_calls_the_model(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await with_profile(api_client)
        body = await generate(api_client, headers)

        trace = await read_trace(api_client, headers, body["data"]["run_id"])

        plan = await stored_plan(db_session)
        calls = {item["node"]: item["model_calls"] for item in trace["steps"]}
        assert [(call["name"], call["version"]) for call in calls["propose_week"]] == [
            (plan.prompt_name, plan.prompt_version)
        ]
        assert all(not calls[node] for node in STEPS if node != "propose_week")

    async def test_the_trace_holds_no_plan_or_profile_content(self, api_client: AsyncClient):
        headers = await with_profile(api_client)
        body = await generate(api_client, headers)

        text = json.dumps(await read_trace(api_client, headers, body["data"]["run_id"]))

        for written in (
            WEEK.summary,
            *(block.title for block in WEEK.blocks),
            *(block.reason for block in WEEK.blocks),
            "Run a half marathon",
            "Only free on weekday evenings",
        ):
            assert written not in text


class TestCrossCheck:
    """The trace, the stored row and the response describe the same plan."""

    async def test_block_counts_and_rejections_agree(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await with_profile(api_client)
        body = await generate(api_client, headers)

        trace = await read_trace(api_client, headers, body["data"]["run_id"])

        plan = await stored_plan(db_session)
        proposed = step(trace, PlanStep.PROPOSE_WEEK)["detail"]
        checked = step(trace, PlanStep.CHECK_WEEK)["detail"]
        stored = step(trace, PlanStep.STORE_PLAN)["detail"]
        assert proposed == {"proposed_blocks": len(WEEK.blocks)}
        # The day-9 block is dropped; an invented citation is stripped, not the block.
        assert checked["rejected"] == {"day_out_of_range": 1, "unknown_evidence": 1}
        assert checked["kept_blocks"] == len(plan.blocks) == len(body["data"]["blocks"])
        assert stored == {"stored_blocks": len(plan.blocks)}
        assert plan.rejected_block_count == sum(checked["rejected"].values())
        evidence = step(trace, PlanStep.COLLECT_EVIDENCE)["detail"]
        assert evidence == {"evidence_items": len(body["data"]["evidence"])}

    async def test_categories_agree(self, api_client: AsyncClient, db_session: AsyncSession):
        headers = await with_profile(api_client)
        body = await generate(api_client, headers)

        trace = await read_trace(api_client, headers, body["data"]["run_id"])

        plan = await stored_plan(db_session)
        traced = step(trace, PlanStep.CHECK_WEEK)["detail"]["categories"]
        assert traced == dict(Counter(block["category"] for block in plan.blocks))
        assert traced == dict(Counter(block["category"] for block in body["data"]["blocks"]))
        assert traced == {"admin": 1, "health": 1}

    async def test_every_category_round_trips_through_trace_row_and_response(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        llm_provider._weeks = [EVERY_CATEGORY]
        headers = await with_profile(api_client)
        body = await generate(api_client, headers)

        trace = await read_trace(api_client, headers, body["data"]["run_id"])
        latest = await api_client.get("/api/v1/plans/week/latest", headers=headers)

        expected = {category.value: 1 for category in BlockCategory}
        plan = await stored_plan(db_session)
        checked = step(trace, PlanStep.CHECK_WEEK)["detail"]
        assert checked["categories"] == expected
        assert checked["rejected"] == {}
        assert dict(Counter(block["category"] for block in plan.blocks)) == expected
        for response in (body["data"], latest.json()["data"]):
            assert dict(Counter(block["category"] for block in response["blocks"])) == expected


class TestFailedRuns:
    async def test_a_model_failure_is_traced_to_the_proposal_step(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers = await with_profile(api_client)
        llm_provider._failure = LLMUnavailableError("provider at 10.0.0.5 timed out")

        response = await api_client.post("/api/v1/plans/week", headers=headers)

        assert response.status_code == 503
        run = await only_run(db_session)
        assert run.kind is RunKind.WEEK_PLAN
        assert run.status is RunStatus.FAILED
        assert run.failed_node == PlanStep.PROPOSE_WEEK.value
        assert run.error_type == "PlanUnavailableError"
        assert run.generation_attempts == 1
        assert await db_session.scalar(select(func.count()).select_from(WeekPlan)) == 0

        trace = await read_trace(api_client, headers, str(run.id))
        assert [(item["node"], item["status"]) for item in trace["steps"]] == [
            ("collect_evidence", "ok"),
            ("propose_week", "failed"),
        ]
        call = step(trace, PlanStep.PROPOSE_WEEK)["model_calls"][0]
        assert (call["status"], call["error_type"]) == ("failed", "LLMUnavailableError")
        assert "10.0.0.5" not in json.dumps(trace)

    async def test_a_week_with_nothing_usable_shows_why_in_the_trace(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        llm_provider._weeks = [
            ProposedWeek(
                summary="Nothing that fits.",
                blocks=[
                    ProposedBlock(
                        day=8,
                        duration_minutes=30,
                        title="Too late",
                        category=BlockCategory.REST,
                        reason="Outside the week.",
                    ),
                    ProposedBlock(
                        day=1,
                        start="23:30",
                        duration_minutes=60,
                        title="Past midnight",
                        category=BlockCategory.REST,
                        reason="Runs into the next day.",
                    ),
                ],
            )
        ]
        headers = await with_profile(api_client)

        response = await api_client.post("/api/v1/plans/week", headers=headers)

        assert response.status_code == 503
        run = await only_run(db_session)
        assert run.status is RunStatus.FAILED
        assert run.failed_node == PlanStep.STORE_PLAN.value
        trace = await read_trace(api_client, headers, str(run.id))
        checked = step(trace, PlanStep.CHECK_WEEK)["detail"]
        assert checked == {
            "kept_blocks": 0,
            "rejected": {"day_out_of_range": 1, "past_midnight": 1},
            "categories": {},
        }
        assert await db_session.scalar(select(func.count()).select_from(WeekPlan)) == 0

    async def test_nothing_to_plan_from_fails_before_any_model_call(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.post("/api/v1/plans/week", headers=headers)

        assert response.status_code == 409
        run = await only_run(db_session)
        assert run.failed_node == PlanStep.COLLECT_EVIDENCE.value
        assert run.error_type == "NothingToPlanFromError"
        assert run.model_calls == 0
        assert run.generation_attempts == 0


class TestOwnership:
    async def test_another_users_plan_run_is_not_found(self, api_client: AsyncClient):
        owner = await with_profile(api_client, "grace@example.com")
        run_id = (await generate(api_client, owner))["data"]["run_id"]
        intruder = await with_profile(api_client, "ada@example.com")

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
    async def test_a_failed_plan_is_traced_and_still_refunded(
        self,
        metered_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
    ):
        """The failed run is committed inside the request; the refund must survive it."""
        headers = await with_profile(metered_client)
        llm_provider._failure = LLMUnavailableError("down")

        response = await metered_client.post("/api/v1/plans/week", headers=headers)

        assert response.status_code == 503
        run = await only_run(db_session)
        assert run.status is RunStatus.FAILED
        owner = run.user_id
        db_session.expire_all()
        used = await db_session.scalar(
            select(UsageCounter.used).where(
                UsageCounter.user_id == owner,
                UsageCounter.feature == Feature.WEEK_PLAN,
                UsageCounter.period_start == usage_period(datetime.now(tz=UTC)).starts_on,
            )
        )
        assert (used or 0) == 0
