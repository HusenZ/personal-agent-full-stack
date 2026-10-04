"""Plans and monthly allowances end to end: real HTTP, real database, a scripted model."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.subscription.service import SubscriptionService
from app.core.config import Settings
from app.domain.subscription.plans import Feature, Plan, limit_for, usage_period
from app.infrastructure.database.models import UsageCounter, User
from app.infrastructure.database.repositories.subscription import SubscriptionRepository
from app.main import create_app
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider
from tests.integration.conftest import sessions_like
from tests.integration.test_chat import authenticate, send
from tests.integration.test_chat_stream import FailingGraph, names, parse, stream
from tests.integration.test_plans import WEEK, with_profile

pytestmark = pytest.mark.integration

FREE_CHAT_LIMIT = limit_for(Plan.FREE, Feature.CHAT_MESSAGE)


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(replies=["A personalised reply."], weeks=[WEEK])


@pytest.fixture
async def metered_client(
    database_settings: Settings, clean_database: None, llm_provider: FakeLLMProvider
) -> AsyncIterator[tuple[AsyncClient, FastAPI]]:
    application = create_app(
        database_settings.model_copy(update={"usage_limits_enabled": True}),
        llm_provider=llm_provider,
        embedding_provider=FakeEmbeddingProvider(),
    )
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, application
    await application.state.database_engine.dispose()


@pytest.fixture
def client(metered_client: tuple[AsyncClient, FastAPI]) -> AsyncClient:
    return metered_client[0]


async def user_id(db_session: AsyncSession, email: str = "ada@example.com") -> uuid.UUID:
    return (await db_session.execute(select(User.id).where(User.email == email))).scalar_one()


async def used(db_session: AsyncSession, owner: uuid.UUID, feature: Feature) -> int:
    db_session.expire_all()
    value = await db_session.scalar(
        select(UsageCounter.used).where(
            UsageCounter.user_id == owner,
            UsageCounter.feature == feature,
            UsageCounter.period_start == usage_period(datetime.now(tz=UTC)).starts_on,
        )
    )
    return value or 0


async def set_used(db_session: AsyncSession, owner: uuid.UUID, feature: Feature, count: int):
    db_session.add(
        UsageCounter(
            user_id=owner,
            feature=feature,
            period_start=usage_period(datetime.now(tz=UTC)).starts_on,
            used=count,
        )
    )
    await db_session.commit()


async def grant(db_session: AsyncSession, owner: uuid.UUID, plan: Plan, expires_at=None):
    service = SubscriptionService(db_session, SubscriptionRepository(db_session))
    await service.grant(owner, plan, expires_at=expires_at)


async def read(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.get("/api/v1/subscription", headers=headers)
    assert response.status_code == 200
    data: dict[str, Any] = response.json()["data"]
    return data


def usage_of(data: dict[str, Any], feature: Feature) -> dict[str, Any]:
    return next(item for item in data["usage"] if item["feature"] == feature.value)


class TestReadingTheSubscription:
    async def test_it_requires_authentication(self, client: AsyncClient):
        assert (await client.get("/api/v1/subscription")).status_code == 401

    async def test_a_new_account_is_on_the_free_plan_with_nothing_used(self, client: AsyncClient):
        data = await read(client, await authenticate(client, "ada@example.com"))

        assert data["plan"] == "free"
        assert data["expires_at"] is None
        assert {item["feature"] for item in data["usage"]} == {f.value for f in Feature}
        chat = usage_of(data, Feature.CHAT_MESSAGE)
        assert chat == {
            "feature": "chat_message",
            "used": 0,
            "limit": FREE_CHAT_LIMIT,
            "remaining": FREE_CHAT_LIMIT,
        }

    async def test_usage_is_counted_per_user(self, client: AsyncClient):
        ada = await authenticate(client, "ada@example.com")
        grace = await authenticate(client, "grace@example.com")

        await send(client, ada, "How should I train?")

        assert usage_of(await read(client, ada), Feature.CHAT_MESSAGE)["used"] == 1
        assert usage_of(await read(client, grace), Feature.CHAT_MESSAGE)["used"] == 0


class TestEnforcing:
    async def test_the_last_unit_is_usable_and_the_next_is_refused(
        self, client: AsyncClient, db_session: AsyncSession, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(client, "ada@example.com")
        owner = await user_id(db_session)
        await set_used(db_session, owner, Feature.CHAT_MESSAGE, FREE_CHAT_LIMIT - 1)

        assert (await send(client, headers, "One more?")).status_code == 201
        prompts_before = len(llm_provider.prompts)
        refused = await send(client, headers, "And another?")

        assert refused.status_code == 402
        error = refused.json()["error"]
        assert error["code"] == "QUOTA_EXCEEDED"
        assert error["details"] == [
            {
                "plan": "free",
                "feature": "chat_message",
                "limit": FREE_CHAT_LIMIT,
                "resets_at": usage_period(datetime.now(tz=UTC)).resets_at.isoformat(),
            }
        ]
        # Refused before any work: the model was never asked.
        assert len(llm_provider.prompts) == prompts_before
        assert await used(db_session, owner, Feature.CHAT_MESSAGE) == FREE_CHAT_LIMIT

    async def test_streaming_draws_on_the_same_allowance(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(client, "ada@example.com")
        owner = await user_id(db_session)

        status_code, _, _ = await stream(client, headers)
        assert status_code == 200
        assert await used(db_session, owner, Feature.CHAT_MESSAGE) == 1

        await set_used_to_limit(db_session, owner)
        status_code, _, body = await stream(client, headers)
        assert status_code == 402
        assert "QUOTA_EXCEEDED" in body

    async def test_features_are_limited_separately(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        headers = await with_profile(client)
        owner = await user_id(db_session)
        await set_used(db_session, owner, Feature.CHAT_MESSAGE, FREE_CHAT_LIMIT)

        response = await client.post("/api/v1/plans/week", headers=headers)

        assert response.status_code == 201
        assert await used(db_session, owner, Feature.WEEK_PLAN) == 1

    async def test_a_week_plan_beyond_the_allowance_is_refused(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        headers = await with_profile(client)
        owner = await user_id(db_session)
        await set_used(
            db_session, owner, Feature.WEEK_PLAN, limit_for(Plan.FREE, Feature.WEEK_PLAN)
        )

        response = await client.post("/api/v1/plans/week", headers=headers)

        assert response.status_code == 402
        assert response.json()["error"]["details"][0]["feature"] == "week_plan"

    async def test_an_analysis_beyond_the_allowance_is_refused(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(client, "ada@example.com")
        owner = await user_id(db_session)
        await set_used(db_session, owner, Feature.ANALYSIS, limit_for(Plan.FREE, Feature.ANALYSIS))

        response = await client.post("/api/v1/analysis", headers=headers)

        assert response.status_code == 402
        assert response.json()["error"]["details"][0]["feature"] == "analysis"

    async def test_concurrent_requests_cannot_overrun_the_limit(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        await authenticate(client, "ada@example.com")
        owner = await user_id(db_session)
        await set_used(db_session, owner, Feature.CHAT_MESSAGE, FREE_CHAT_LIMIT - 1)
        factory = sessions_like(db_session)

        async def attempt() -> bool:
            async with factory() as session:
                service = SubscriptionService(session, SubscriptionRepository(session))
                try:
                    await service.consume(owner, Feature.CHAT_MESSAGE)
                except Exception:
                    return False
                return True

        outcomes = await asyncio.gather(*(attempt() for _ in range(5)))

        assert outcomes.count(True) == 1
        assert await used(db_session, owner, Feature.CHAT_MESSAGE) == FREE_CHAT_LIMIT


class TestFailuresCostNothing:
    async def test_a_request_the_service_rejects_is_refunded(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        # No profile, so there is nothing to plan from: 409 after the unit was taken.
        headers = await authenticate(client, "ada@example.com")
        owner = await user_id(db_session)

        response = await client.post("/api/v1/plans/week", headers=headers)

        assert response.status_code == 409
        assert await used(db_session, owner, Feature.WEEK_PLAN) == 0

    async def test_an_invalid_message_uses_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(client, "ada@example.com")
        owner = await user_id(db_session)

        response = await client.post("/api/v1/chat", headers=headers, json={"message": ""})

        assert response.status_code == 422
        assert await used(db_session, owner, Feature.CHAT_MESSAGE) == 0

    async def test_a_foreign_conversation_uses_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(client, "ada@example.com")
        owner = await user_id(db_session)

        response = await send(client, headers, "Hello?", conversation_id=uuid.uuid4())

        assert response.status_code == 404
        assert await used(db_session, owner, Feature.CHAT_MESSAGE) == 0

    async def test_a_stream_that_fails_part_way_is_refunded(
        self,
        metered_client: tuple[AsyncClient, FastAPI],
        db_session: AsyncSession,
    ):
        client, application = metered_client
        headers = await authenticate(client, "ada@example.com")
        owner = await user_id(db_session)
        application.state.conversation_graph = FailingGraph()

        status_code, _, body = await stream(client, headers)

        assert status_code == 200
        assert names(parse(body))[-1] == "error"
        assert await used(db_session, owner, Feature.CHAT_MESSAGE) == 0


class TestPlans:
    async def test_pro_raises_the_limits(self, client: AsyncClient, db_session: AsyncSession):
        headers = await authenticate(client, "ada@example.com")
        owner = await user_id(db_session)
        await set_used(db_session, owner, Feature.CHAT_MESSAGE, FREE_CHAT_LIMIT)
        ends = datetime.now(tz=UTC) + timedelta(days=30)
        await grant(db_session, owner, Plan.PRO, expires_at=ends)

        data = await read(client, headers)
        assert data["plan"] == "pro"
        assert datetime.fromisoformat(data["expires_at"]) == ends
        assert usage_of(data, Feature.CHAT_MESSAGE)["limit"] == limit_for(
            Plan.PRO, Feature.CHAT_MESSAGE
        )
        assert (await send(client, headers, "Still here?")).status_code == 201

    async def test_an_ended_grant_is_back_on_free(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(client, "ada@example.com")
        owner = await user_id(db_session)
        await grant(db_session, owner, Plan.PRO, expires_at=datetime.now(tz=UTC) - timedelta(1))
        await set_used(db_session, owner, Feature.CHAT_MESSAGE, FREE_CHAT_LIMIT)

        data = await read(client, headers)
        assert data["plan"] == "free"
        assert data["expires_at"] is None
        assert (await send(client, headers, "Hello?")).status_code == 402

    async def test_a_downgrade_never_reports_negative_remaining(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(client, "ada@example.com")
        owner = await user_id(db_session)
        await set_used(db_session, owner, Feature.CHAT_MESSAGE, FREE_CHAT_LIMIT + 40)

        chat = usage_of(await read(client, headers), Feature.CHAT_MESSAGE)

        assert chat["remaining"] == 0

    async def test_granting_again_replaces_the_plan(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(client, "ada@example.com")
        owner = await user_id(db_session)
        await grant(db_session, owner, Plan.PRO)
        await grant(db_session, owner, Plan.FREE)

        assert (await read(client, headers))["plan"] == "free"


async def set_used_to_limit(db_session: AsyncSession, owner: uuid.UUID) -> None:
    db_session.expire_all()
    counter = await db_session.get(
        UsageCounter,
        (owner, Feature.CHAT_MESSAGE, usage_period(datetime.now(tz=UTC)).starts_on),
    )
    assert counter is not None
    counter.used = FREE_CHAT_LIMIT
    await db_session.commit()
