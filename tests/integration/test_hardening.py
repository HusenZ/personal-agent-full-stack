"""Reliability and security properties of the running application."""

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

from app.core.config import Settings
from app.infrastructure.database.models import (
    AgentRun,
    BackgroundJob,
    Conversation,
    JobKind,
    JobStatus,
    Message,
    RunKind,
    RunStatus,
    User,
)
from app.infrastructure.llm.prompt import Prompt
from app.main import create_app
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider
from tests.integration.conftest import app_worker
from tests.integration.test_chat import authenticate

pytestmark = pytest.mark.integration


class PoolObservingModel(FakeLLMProvider):
    """Records how many connections are checked out whenever the model is called."""

    def __init__(self) -> None:
        super().__init__(replies=["A reply."])
        self.app: FastAPI | None = None
        self.checked_out: list[int] = []

    def observe(self) -> None:
        assert self.app is not None
        self.checked_out.append(self.app.state.database_engine.pool.checkedout())

    async def generate_structured(self, prompt: Prompt, schema: Any) -> Any:
        self.observe()
        return await super().generate_structured(prompt, schema)

    async def generate_text(self, prompt: Prompt) -> str:
        self.observe()
        return await super().generate_text(prompt)


class TestConnectionsAreNotHeldAcrossModelCalls:
    @pytest.fixture
    def llm_provider(self) -> PoolObservingModel:
        return PoolObservingModel()

    @pytest.mark.parametrize("path", ["/api/v1/chat", "/api/v1/chat/stream"])
    async def test_no_connection_is_checked_out_while_the_model_works(
        self,
        api_app: FastAPI,
        plain_client: AsyncClient,
        llm_provider: PoolObservingModel,
        path: str,
    ):
        """Held connections capped concurrent turns at the pool size, then failed."""
        llm_provider.app = api_app
        headers = await authenticate(plain_client, "ada@example.com")
        first = await plain_client.post(path, headers=headers, json={"message": "hello"})
        assert first.status_code in (200, 201)

        # A follow-up too, so an existing conversation's path is covered.
        conversation_id = (await plain_client.get("/api/v1/conversations", headers=headers)).json()[
            "data"
        ][0]["id"]
        await plain_client.post(
            path, headers=headers, json={"message": "and again", "conversation_id": conversation_id}
        )

        assert llm_provider.checked_out, "the model was never called"
        assert set(llm_provider.checked_out) == {0}


class SlowReplies(FakeLLMProvider):
    async def generate_text(self, prompt: Prompt) -> str:
        await asyncio.sleep(0.2)
        return await super().generate_text(prompt)


class TestConcurrentSendsToOneConversation:
    @pytest.fixture
    def llm_provider(self) -> SlowReplies:
        return SlowReplies(replies=["A reply."])

    async def test_both_messages_are_stored_in_order(
        self, plain_client: AsyncClient, db_session: AsyncSession
    ):
        """Both used to read the same count, and the second failed with a 500."""
        headers = await authenticate(plain_client, "ada@example.com")
        first = await plain_client.post("/api/v1/chat", headers=headers, json={"message": "hi"})
        conversation_id = first.json()["data"]["conversation_id"]

        responses = await asyncio.gather(
            *(
                plain_client.post(
                    "/api/v1/chat",
                    headers=headers,
                    json={"message": f"quick {index}", "conversation_id": conversation_id},
                )
                for index in range(3)
            )
        )

        assert [response.status_code for response in responses] == [201, 201, 201]
        positions = (
            await db_session.scalars(select(Message.position).order_by(Message.position))
        ).all()
        assert positions == list(range(8))
        conversation = (await db_session.scalars(select(Conversation))).one()
        assert conversation.message_count == 8


@pytest.fixture
async def small_limit_client(
    database_settings: Settings, clean_database: None
) -> AsyncIterator[AsyncClient]:
    app = create_app(
        database_settings.model_copy(update={"max_request_body_bytes": 2048}),
        llm_provider=FakeLLMProvider(),
        embedding_provider=FakeEmbeddingProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client
    await app.state.database_engine.dispose()


class TestRequestBodyLimit:
    async def test_a_declared_oversize_is_refused_in_the_usual_envelope(
        self, small_limit_client: AsyncClient
    ):
        response = await small_limit_client.post(
            "/api/v1/auth/login",
            content=b"x" * 5000,
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 413
        assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
        assert response.json()["meta"]["request_id"] == response.headers["x-request-id"]

    async def test_an_undeclared_oversize_is_refused_while_it_streams(
        self, small_limit_client: AsyncClient
    ):
        async def chunks() -> AsyncIterator[bytes]:
            for _ in range(10):
                yield b"x" * 1024

        response = await small_limit_client.post(
            "/api/v1/auth/login", content=chunks(), headers={"content-type": "application/json"}
        )

        assert response.status_code == 413

    async def test_ordinary_requests_are_unaffected(self, small_limit_client: AsyncClient):
        response = await small_limit_client.post(
            "/api/v1/auth/register",
            json={"email": "ada@example.com", "password": "a-sufficiently-long-password"},
        )

        assert response.status_code == 201


class TestSecurityHeaders:
    async def test_json_responses_are_not_sniffed_or_stored(self, api_client: AsyncClient):
        response = await api_client.get("/health")

        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"

    async def test_a_response_with_its_own_caching_keeps_it(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.post(
            "/api/v1/chat/stream", headers=headers, json={"message": "hi"}
        )

        assert response.headers["cache-control"] == "no-cache, no-transform"
        assert response.headers["x-content-type-options"] == "nosniff"


class TestRetention:
    async def test_only_records_past_their_retention_are_pruned(
        self, api_app: FastAPI, db_session: AsyncSession, clean_database: None
    ):
        user = User(email="ada@example.com", password_hash="unused")
        db_session.add(user)
        await db_session.flush()
        now = datetime.now(tz=UTC)

        def run(age_days: int) -> AgentRun:
            return AgentRun(
                id=uuid.uuid4(),
                user_id=user.id,
                kind=RunKind.CONVERSATION_TURN,
                status=RunStatus.COMPLETED,
                started_at=now - timedelta(days=age_days),
                duration_ms=1.0,
                model_calls=0,
                retrieval_attempts=0,
                generation_attempts=0,
            )

        def job(status: JobStatus, age_days: int) -> BackgroundJob:
            finished = status in (JobStatus.SUCCEEDED, JobStatus.FAILED)
            return BackgroundJob(
                user_id=user.id,
                kind=JobKind.MEMORY_EXTRACTION,
                status=status,
                payload={},
                max_attempts=3,
                finished_at=now - timedelta(days=age_days) if finished else None,
                run_after=now - timedelta(days=age_days),
            )

        old_run, recent_run = run(45), run(5)
        old_done, recent_done = job(JobStatus.SUCCEEDED, 10), job(JobStatus.SUCCEEDED, 1)
        old_failed, old_pending = job(JobStatus.FAILED, 10), job(JobStatus.PENDING, 10)
        db_session.add_all([old_run, recent_run, old_done, recent_done, old_failed, old_pending])
        await db_session.commit()

        recent_run_id = recent_run.id
        kept_job_ids = {recent_done.id, old_pending.id}

        removed = await app_worker(api_app).prune()

        assert removed == {"runs": 1, "jobs": 2}
        db_session.expire_all()
        assert (await db_session.scalars(select(AgentRun.id))).all() == [recent_run_id]
        remaining = set((await db_session.scalars(select(BackgroundJob.id))).all())
        assert remaining == kept_job_ids
