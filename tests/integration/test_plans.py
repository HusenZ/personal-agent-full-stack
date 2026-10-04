"""Week plans end to end: real HTTP, real database, a scripted model."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.planning.week import BlockCategory, ProposedBlock, ProposedWeek
from app.infrastructure.database.models import WeekPlan
from app.infrastructure.llm.exceptions import LLMUnavailableError
from tests.fakes import FakeLLMProvider
from tests.integration.test_chat import authenticate

pytestmark = pytest.mark.integration

ZONE = "Asia/Kolkata"

WEEK = ProposedWeek(
    summary="Three runs around your evenings, with Sunday off.",
    blocks=[
        ProposedBlock(
            day=2,
            start="19:00",
            duration_minutes=40,
            title="Easy run",
            category=BlockCategory.HEALTH,
            reason="Builds the base for your half marathon.",
            evidence_ids=["goal:1", "constraint:1"],
        ),
        ProposedBlock(
            day=0,
            start=None,
            duration_minutes=30,
            title="Plan the week's runs",
            category=BlockCategory.ADMIN,
            reason="Sets up the week.",
            evidence_ids=["goal:1", "goal:7"],
        ),
        ProposedBlock(
            day=9,
            start="07:00",
            duration_minutes=30,
            title="Out of range",
            category=BlockCategory.HEALTH,
            reason="Should be rejected.",
        ),
    ],
)


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(weeks=[WEEK])


async def with_profile(client: AsyncClient, email: str = "ada@example.com") -> dict[str, str]:
    headers = await authenticate(client, email)
    await client.patch(
        "/api/v1/profile",
        headers=headers,
        json={
            "timezone": ZONE,
            "goals": [{"name": "Run a half marathon", "priority": "high"}],
            "constraints": ["Only free on weekday evenings"],
        },
    )
    return headers


async def generate(client: AsyncClient, headers: dict[str, str]):
    return await client.post("/api/v1/plans/week", headers=headers)


class TestGenerating:
    async def test_a_plan_is_placed_on_the_users_own_week(self, api_client: AsyncClient):
        headers = await with_profile(api_client)

        response = await generate(api_client, headers)

        assert response.status_code == 201
        plan = response.json()["data"]
        today = datetime.now(ZoneInfo(ZONE)).date()
        assert plan["starts_on"] == today.isoformat()
        assert plan["ends_on"] == (today + timedelta(days=6)).isoformat()
        assert plan["timezone"] == ZONE
        assert [block["title"] for block in plan["blocks"]] == ["Plan the week's runs", "Easy run"]
        assert plan["blocks"][1]["date"] == (today + timedelta(days=2)).isoformat()
        assert plan["blocks"][0]["evidence_ids"] == ["goal:1"], "an invented citation is dropped"
        assert all(block["done"] is False for block in plan["blocks"])
        assert plan["blocks"][0]["starts_at"] is None, "an any-time block has no instant"
        run = plan["blocks"][1]
        starts_at = datetime.fromisoformat(run["starts_at"])
        assert starts_at.utcoffset() == timedelta(hours=5, minutes=30)
        assert (starts_at.hour, starts_at.minute) == (19, 0)
        assert datetime.fromisoformat(run["ends_at"]) - starts_at == timedelta(minutes=40)
        assert {item["id"] for item in plan["evidence"]} >= {"goal:1", "constraint:1"}

    async def test_the_model_is_given_the_days_and_the_profile(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await with_profile(api_client)

        await generate(api_client, headers)

        prompt = llm_provider.prompts_named("planning.week")[-1].user
        today = datetime.now(ZoneInfo(ZONE)).date()
        assert f"0 = {today.strftime('%A')}" in prompt
        assert "Run a half marathon" in prompt
        assert "Only free on weekday evenings" in prompt

    async def test_nothing_known_means_no_plan(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        response = await generate(api_client, headers)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INSUFFICIENT_CONTEXT"

    async def test_a_model_failure_is_reported_and_nothing_stored(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers = await with_profile(api_client)
        llm_provider._failure = LLMUnavailableError("down")

        response = await generate(api_client, headers)

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "PLAN_UNAVAILABLE"
        assert await db_session.scalar(select(func.count()).select_from(WeekPlan)) == 0

    async def test_a_proposal_with_nothing_usable_is_not_stored(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers = await with_profile(api_client)
        llm_provider._weeks = [ProposedWeek(summary="Nothing.", blocks=[])]

        response = await generate(api_client, headers)

        assert response.status_code == 503
        assert await db_session.scalar(select(func.count()).select_from(WeekPlan)) == 0


class TestReadingAndTicking:
    async def test_there_is_no_plan_until_one_is_made(self, api_client: AsyncClient):
        headers = await with_profile(api_client)

        response = await api_client.get("/api/v1/plans/week/latest", headers=headers)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "PLAN_NOT_FOUND"

    async def test_a_ticked_block_stays_ticked(self, api_client: AsyncClient):
        headers = await with_profile(api_client)
        plan = (await generate(api_client, headers)).json()["data"]
        block_id = plan["blocks"][1]["id"]

        response = await api_client.patch(
            f"/api/v1/plans/{plan['id']}/blocks/{block_id}", headers=headers, json={"done": True}
        )

        assert response.status_code == 200
        latest = (await api_client.get("/api/v1/plans/week/latest", headers=headers)).json()["data"]
        assert [block["done"] for block in latest["blocks"]] == [False, True]

    async def test_an_unknown_block_is_not_found(self, api_client: AsyncClient):
        headers = await with_profile(api_client)
        plan = (await generate(api_client, headers)).json()["data"]

        response = await api_client.patch(
            f"/api/v1/plans/{plan['id']}/blocks/nope", headers=headers, json={"done": True}
        )

        assert response.json()["error"]["code"] == "PLAN_BLOCK_NOT_FOUND"


class TestOwnership:
    async def test_another_users_plan_cannot_be_read_or_ticked(self, api_client: AsyncClient):
        owner = await with_profile(api_client, "grace@example.com")
        plan = (await generate(api_client, owner)).json()["data"]
        intruder = await with_profile(api_client, "ada@example.com")

        latest = await api_client.get("/api/v1/plans/week/latest", headers=intruder)
        tick = await api_client.patch(
            f"/api/v1/plans/{plan['id']}/blocks/{plan['blocks'][0]['id']}",
            headers=intruder,
            json={"done": True},
        )

        assert latest.status_code == 404
        assert tick.status_code == 404
        assert tick.json()["error"]["code"] == "PLAN_NOT_FOUND"

    async def test_plans_require_a_token(self, api_client: AsyncClient):
        assert (await api_client.post("/api/v1/plans/week")).status_code == 401
