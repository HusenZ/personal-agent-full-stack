import asyncio
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.infrastructure.cache.rate_limiter import RateLimiter, RateLimitRule
from app.main import create_app
from tests.fakes import FakeLLMProvider
from tests.integration.conftest import TEST_REDIS_URL

pytestmark = pytest.mark.integration

PASSWORD = "a-sufficiently-long-password"


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)

    try:
        await client.ping()
    except (RedisError, OSError):
        await client.aclose()
        pytest.skip(f"no test Redis reachable at {TEST_REDIS_URL}")

    # Fresh window per test.
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
def limited_settings(database_settings: Settings) -> Settings:
    return database_settings.model_copy(
        update={"rate_limit_enabled": True, "redis_url": TEST_REDIS_URL}
    )


@pytest.fixture
async def limited_app(
    limited_settings: Settings,
    clean_database: None,
    redis_client: Redis,
    llm_provider: FakeLLMProvider,
) -> AsyncIterator[FastAPI]:
    application = create_app(limited_settings, llm_provider=llm_provider)

    yield application

    await application.state.redis_client.aclose()
    await application.state.database_engine.dispose()


@pytest.fixture
async def proxied_client(
    limited_settings: Settings,
    clean_database: None,
    redis_client: Redis,
    llm_provider: FakeLLMProvider,
) -> AsyncIterator[AsyncClient]:
    """A client whose forwarded address is honoured, as behind a trusted proxy."""
    application = create_app(
        limited_settings.model_copy(update={"trust_proxy_headers": True}),
        llm_provider=llm_provider,
    )

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    await application.state.redis_client.aclose()
    await application.state.database_engine.dispose()


@pytest.fixture
async def limited_client(limited_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=limited_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def register(client: AsyncClient, email: str):
    return await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})


async def login(client: AsyncClient, email: str, password: str = PASSWORD):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


class TestLimiterPrimitive:
    async def test_requests_are_allowed_up_to_the_limit(self, redis_client: Redis):
        limiter = RateLimiter(redis_client, fail_open=True)
        rule = RateLimitRule(name="probe", limit=3, window_seconds=60)

        decisions = [await limiter.check(rule, "someone") for _ in range(4)]

        assert [decision.allowed for decision in decisions] == [True, True, True, False]

    async def test_remaining_counts_down(self, redis_client: Redis):
        limiter = RateLimiter(redis_client, fail_open=True)
        rule = RateLimitRule(name="probe", limit=3, window_seconds=60)

        decisions = [await limiter.check(rule, "someone") for _ in range(3)]

        assert [decision.remaining for decision in decisions] == [2, 1, 0]

    async def test_identities_are_counted_separately(self, redis_client: Redis):
        limiter = RateLimiter(redis_client, fail_open=True)
        rule = RateLimitRule(name="probe", limit=1, window_seconds=60)

        await limiter.check(rule, "first")
        decision = await limiter.check(rule, "second")

        assert decision.allowed

    async def test_rules_are_counted_separately(self, redis_client: Redis):
        limiter = RateLimiter(redis_client, fail_open=True)
        exhausted = RateLimitRule(name="one", limit=1, window_seconds=60)
        other = RateLimitRule(name="two", limit=1, window_seconds=60)

        await limiter.check(exhausted, "someone")
        await limiter.check(exhausted, "someone")

        assert (await limiter.check(other, "someone")).allowed

    async def test_a_rejection_reports_how_long_to_wait(self, redis_client: Redis):
        limiter = RateLimiter(redis_client, fail_open=True)
        rule = RateLimitRule(name="probe", limit=1, window_seconds=60)

        await limiter.check(rule, "someone")
        decision = await limiter.check(rule, "someone")

        assert 0 < decision.retry_after_seconds <= 60

    async def test_the_window_slides(self, redis_client: Redis):
        """A fixed window would let a full second budget through at the boundary."""
        limiter = RateLimiter(redis_client, fail_open=True)
        rule = RateLimitRule(name="probe", limit=2, window_seconds=1)

        assert (await limiter.check(rule, "someone")).allowed
        assert (await limiter.check(rule, "someone")).allowed
        assert not (await limiter.check(rule, "someone")).allowed

        await asyncio.sleep(1.1)

        assert (await limiter.check(rule, "someone")).allowed

    async def test_concurrent_requests_cannot_exceed_the_limit(self, redis_client: Redis):
        """Read-then-write without atomicity would let simultaneous requests race."""
        limiter = RateLimiter(redis_client, fail_open=True)
        rule = RateLimitRule(name="probe", limit=5, window_seconds=60)

        decisions = await asyncio.gather(*(limiter.check(rule, "someone") for _ in range(25)))

        assert sum(decision.allowed for decision in decisions) == 5


class TestBackendUnavailable:
    async def test_failing_open_allows_the_request(self):
        """A cache outage must not take authentication down with it."""
        limiter = RateLimiter(None, fail_open=True)
        rule = RateLimitRule(name="probe", limit=1, window_seconds=60)

        decisions = [await limiter.check(rule, "someone") for _ in range(5)]

        assert all(decision.allowed for decision in decisions)

    async def test_failing_closed_rejects_the_request(self):
        limiter = RateLimiter(None, fail_open=False)
        rule = RateLimitRule(name="probe", limit=1, window_seconds=60)

        decision = await limiter.check(rule, "someone")

        assert not decision.allowed
        assert decision.retry_after_seconds > 0

    async def test_an_unreachable_redis_falls_back_rather_than_raising(self):
        unreachable = Redis.from_url(
            "redis://127.0.0.1:1/0", socket_connect_timeout=0.05, socket_timeout=0.05
        )
        limiter = RateLimiter(unreachable, fail_open=True)

        try:
            decision = await limiter.check(
                RateLimitRule(name="probe", limit=1, window_seconds=60), "someone"
            )
        finally:
            await unreachable.aclose()

        assert decision.allowed


class TestRegistrationLimit:
    async def test_repeated_registration_from_one_address_is_stopped(
        self, limited_client: AsyncClient
    ):
        statuses = [
            (await register(limited_client, f"user{index}@example.com")).status_code
            for index in range(7)
        ]

        assert statuses.count(201) == 5
        assert statuses[-1] == 429

    async def test_the_rejection_says_how_long_to_wait(self, limited_client: AsyncClient):
        for index in range(6):
            response = await register(limited_client, f"user{index}@example.com")

        assert response.status_code == 429
        assert int(response.headers["retry-after"]) > 0

    async def test_the_rejection_uses_the_standard_error_envelope(
        self, limited_client: AsyncClient
    ):
        for index in range(6):
            response = await register(limited_client, f"user{index}@example.com")

        assert response.json()["error"]["code"] == "RATE_LIMITED"
        assert response.json()["meta"]["request_id"]

    async def test_allowed_responses_advertise_the_budget(self, limited_client: AsyncClient):
        response = await register(limited_client, "first@example.com")

        assert response.headers["x-ratelimit-limit"] == "5"
        assert response.headers["x-ratelimit-remaining"] == "4"


class TestLoginLimit:
    async def test_password_guessing_against_one_account_is_stopped(
        self, limited_client: AsyncClient
    ):
        await register(limited_client, "target@example.com")

        statuses = [
            (await login(limited_client, "target@example.com", "wrong-password-guess")).status_code
            for _ in range(7)
        ]

        assert statuses.count(401) == 5
        assert statuses[-1] == 429

    async def test_a_distributed_attempt_is_still_stopped(self, proxied_client: AsyncClient):
        """Every guess arrives from a different address, so only the per-account
        rule can catch it. That is the rule's entire reason for existing."""
        await register(proxied_client, "target@example.com")

        statuses = []
        for index in range(8):
            response = await proxied_client.post(
                "/api/v1/auth/login",
                json={"email": "target@example.com", "password": "wrong-password-guess"},
                headers={"X-Forwarded-For": f"198.51.100.{index + 1}"},
            )
            statuses.append(response.status_code)

        assert statuses.count(401) == 5
        assert statuses[-1] == 429

    async def test_a_distributed_attempt_would_evade_the_per_address_rule_alone(
        self, proxied_client: AsyncClient
    ):
        """Shows the per-address rule really is blind to this, so the account
        rule is not redundant with it."""
        statuses = []
        for index in range(8):
            response = await proxied_client.post(
                "/api/v1/auth/register",
                json={"email": f"u{index}@example.com", "password": PASSWORD},
                headers={"X-Forwarded-For": f"198.51.100.{index + 1}"},
            )
            statuses.append(response.status_code)

        assert statuses.count(201) == 8

    async def test_email_case_does_not_create_a_second_budget(self, limited_client: AsyncClient):
        await register(limited_client, "target@example.com")
        for _ in range(5):
            await login(limited_client, "target@example.com", "wrong-password-guess")

        response = await login(limited_client, "TARGET@Example.COM", "wrong-password-guess")

        assert response.status_code == 429

    async def test_another_account_is_unaffected(self, limited_client: AsyncClient):
        await register(limited_client, "target@example.com")
        await register(limited_client, "bystander@example.com")
        for _ in range(5):
            await login(limited_client, "target@example.com", "wrong-password-guess")

        response = await login(limited_client, "bystander@example.com")

        assert response.status_code == 200


class TestModelEndpointLimits:
    async def test_analysis_generation_is_capped_per_user(self, limited_client: AsyncClient):
        await register(limited_client, "ada@example.com")
        tokens = (await login(limited_client, "ada@example.com")).json()["data"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        await limited_client.patch(
            "/api/v1/profile",
            headers=headers,
            json={"goals": [{"name": "Move into AI engineering", "priority": "high"}]},
        )

        statuses = [
            (await limited_client.post("/api/v1/analysis", headers=headers)).status_code
            for _ in range(12)
        ]

        assert statuses.count(429) > 0
        assert statuses.count(201) == 10

    async def test_one_users_budget_does_not_consume_anothers(self, limited_client: AsyncClient):
        async def prepare(email: str) -> dict[str, str]:
            await register(limited_client, email)
            tokens = (await login(limited_client, email)).json()["data"]
            headers = {"Authorization": f"Bearer {tokens['access_token']}"}
            await limited_client.patch(
                "/api/v1/profile",
                headers=headers,
                json={"goals": [{"name": "A goal", "priority": "high"}]},
            )
            return headers

        ada = await prepare("ada@example.com")
        grace = await prepare("grace@example.com")
        for _ in range(11):
            await limited_client.post("/api/v1/analysis", headers=ada)

        response = await limited_client.post("/api/v1/analysis", headers=grace)

        assert response.status_code == 201


class TestUnprotectedReads:
    async def test_reading_a_profile_is_not_rate_limited(self, limited_client: AsyncClient):
        """Cheap authenticated reads should not be throttled alongside paid writes."""
        await register(limited_client, "ada@example.com")
        tokens = (await login(limited_client, "ada@example.com")).json()["data"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        statuses = [
            (await limited_client.get("/api/v1/profile", headers=headers)).status_code
            for _ in range(30)
        ]

        assert set(statuses) == {200}


class TestReadiness:
    async def test_redis_is_reported_when_rate_limiting_is_on(self, limited_client: AsyncClient):
        response = await limited_client.get("/ready")

        assert response.status_code == 200
        assert response.json()["data"]["checks"]["redis"] == "ok"

    async def test_redis_is_not_reported_when_rate_limiting_is_off(self, api_client: AsyncClient):
        """A deployment that turned limiting off is not unhealthy for lacking Redis."""
        response = await api_client.get("/ready")

        assert "redis" not in response.json()["data"]["checks"]


class TestChatStreamLimits:
    async def test_streaming_and_plain_chat_draw_on_one_budget(self, limited_client: AsyncClient):
        """Otherwise either endpoint would be a way around the other's limit."""
        await register(limited_client, "ada@example.com")
        token = (await login(limited_client, "ada@example.com")).json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        plain = await limited_client.post("/api/v1/chat", headers=headers, json={"message": "hi"})
        streamed = await limited_client.post(
            "/api/v1/chat/stream", headers=headers, json={"message": "hi"}
        )

        assert streamed.status_code == 200
        assert streamed.headers["x-ratelimit-limit"] == plain.headers["x-ratelimit-limit"]
        assert int(streamed.headers["x-ratelimit-remaining"]) == (
            int(plain.headers["x-ratelimit-remaining"]) - 1
        )
