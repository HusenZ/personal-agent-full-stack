"""A user can take a copy of everything held about them.

The account is populated through the public API wherever one exists, so the
export is checked against what the other endpoints say the user has, not
against rows arranged to match it.
"""

import asyncio
import json
import re
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.api.v1 import account as account_routes
from app.application.subscription.service import SubscriptionService
from app.core.config import Settings
from app.domain.analysis.claims import AnalysisClaim, AnalysisResult, AnalysisSection, ClaimType
from app.domain.conversation.context import MessageRole
from app.domain.memory.decisions import MemoryJudgment, MemoryVerdict, PairJudgment
from app.domain.memory.types import (
    MemoryCandidate,
    MemoryDurability,
    MemoryExtraction,
    MemorySource,
    MemoryStatus,
    MemoryType,
)
from app.domain.onboarding.context import ExtractedGoal, ExtractionResult
from app.domain.planning.week import BlockCategory, ProposedBlock, ProposedWeek
from app.domain.subscription.plans import Feature, Plan, usage_period
from app.infrastructure.database.models import (
    Analysis,
    Conversation,
    Event,
    Memory,
    Message,
    OnboardingSession,
    OnboardingStatus,
    Profile,
    RefreshToken,
    UsageCounter,
    User,
    WeekPlan,
)
from app.infrastructure.database.repositories.conversation import ConversationRepository
from app.infrastructure.database.repositories.event import EventRepository
from app.infrastructure.database.repositories.memory import MemoryRepository
from app.infrastructure.database.repositories.subscription import SubscriptionRepository
from app.infrastructure.database.session import create_session_factory
from app.main import create_app
from app.schemas.common import DataResponse
from app.schemas.export import AccountExportResponse
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider

pytestmark = pytest.mark.integration

PASSWORD = "a-sufficiently-long-password"
EXPORT = "/api/v1/account/export"


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    """Enough scripted model output for one account to use every feature once."""
    return FakeLLMProvider(
        replies=["Noted."],
        extractions=[
            ExtractionResult(goals=[ExtractedGoal(name="Run a marathon", confidence=0.9)])
        ],
        questions=["What gets in the way?"],
        analyses=[
            AnalysisResult(
                claims=[
                    AnalysisClaim(
                        claim_type=ClaimType.FACT,
                        content="Wants to run a marathon",
                        confidence=0.9,
                        evidence_ids=["goal:1"],
                        section=AnalysisSection.GOALS,
                    )
                ]
            )
        ],
        weeks=[
            ProposedWeek(
                summary="Three runs.",
                blocks=[
                    ProposedBlock(
                        day=1,
                        start="07:00",
                        duration_minutes=40,
                        title="Easy run",
                        category=BlockCategory.HEALTH,
                        reason="Builds the base.",
                        evidence_ids=["goal:1"],
                    )
                ],
            )
        ],
    )


async def authenticate(client: AsyncClient, email: str) -> tuple[dict[str, str], str]:
    """Register and log in. Returns the auth header and the refresh token."""
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    tokens = response.json()["data"]
    return {"Authorization": f"Bearer {tokens['access_token']}"}, tokens["refresh_token"]


def memory(content: str, **overrides: Any) -> MemoryCandidate:
    fields: dict[str, Any] = {
        "content": content,
        "memory_type": MemoryType.GOAL,
        "durability": MemoryDurability.LONG_TERM,
        "importance": 0.8,
        "confidence": 0.9,
    }
    return MemoryCandidate(**(fields | overrides))


async def use_every_feature(
    client: AsyncClient,
    headers: dict[str, str],
    llm_provider: FakeLLMProvider,
    db_session: AsyncSession,
    *,
    label: str,
) -> None:
    """Leave a trace in every table the export covers, tagged with ``label``."""
    await client.patch(
        "/api/v1/profile",
        headers=headers,
        json={
            "timezone": "Asia/Kolkata",
            "goals": [{"name": f"{label} goal", "priority": "high"}],
            "constraints": [f"{label} constraint"],
            "preferences": {"style": f"{label} preference"},
        },
    )

    await client.post("/api/v1/onboarding/start", headers=headers)
    await client.post(
        "/api/v1/onboarding/message", headers=headers, json={"answer": f"{label} answer"}
    )

    # One exchange that is remembered, with a countable event alongside it.
    llm_provider.queue_memory_extractions(
        MemoryExtraction(
            candidates=[
                memory(f"{label} is staying in mobile development"),
                memory(
                    f"{label} studied for two hours",
                    memory_type=MemoryType.EVENT,
                    durability=MemoryDurability.SHORT_TERM,
                    activity="study",
                    quantity=2,
                    unit="hours",
                    days_ago=0,
                ),
            ]
        )
    )
    await client.post("/api/v1/chat", headers=headers, json={"message": f"{label} message"})

    # A second exchange whose memory supersedes the first, so history exists.
    first_id = (await client.get("/api/v1/memories?memory_type=goal", headers=headers)).json()[
        "data"
    ][0]["id"]
    llm_provider.queue_memory_extractions(
        MemoryExtraction(candidates=[memory(f"{label} wants to move into AI engineering")])
    )
    llm_provider.queue_judgments(
        MemoryJudgment(
            judgments=[PairJudgment(memory_id=first_id, verdict=MemoryVerdict.CONTRADICTS)]
        )
    )
    await client.post("/api/v1/chat", headers=headers, json={"message": f"{label} follow-up"})

    await client.post("/api/v1/analysis", headers=headers)
    await client.post("/api/v1/plans/week", headers=headers)

    user_id = (
        await db_session.execute(select(User.id).where(User.email == f"{label}@example.com"))
    ).scalar_one()
    await SubscriptionService(db_session, SubscriptionRepository(db_session)).grant(
        user_id,
        Plan.PRO,
        expires_at=datetime.now(tz=UTC) + timedelta(days=30),
        source=f"{label}-grant",
    )
    db_session.add(
        UsageCounter(
            user_id=user_id,
            feature=Feature.CHAT_MESSAGE,
            period_start=usage_period(datetime.now(tz=UTC)).starts_on,
            used=2,
        )
    )
    await db_session.commit()


def walk_strings(value: Any) -> list[str]:
    """Every string anywhere in a JSON document, keys included."""
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in [str(key), *walk_strings(item)]]
    if isinstance(value, list):
        return [text for item in value for text in walk_strings(item)]
    return [value] if isinstance(value, str) else []


class TestAuthorization:
    async def test_the_export_requires_a_token(self, api_client: AsyncClient):
        response = await api_client.get(EXPORT)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"

    async def test_an_expired_or_forged_token_is_refused(self, api_client: AsyncClient):
        response = await api_client.get(EXPORT, headers={"Authorization": "Bearer not-a-token"})

        assert response.status_code == 401


class TestAFreshAccount:
    async def test_the_document_is_complete_but_empty(self, api_client: AsyncClient):
        headers, _ = await authenticate(api_client, "ada@example.com")

        response = await api_client.get(EXPORT, headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["request_id"]
        export = body["data"]
        assert export["format_version"] == 1
        assert datetime.fromisoformat(export["exported_at"]).tzinfo is not None
        assert export["account"]["email"] == "ada@example.com"
        assert export["profile"] == {
            "timezone": "UTC",
            "goals": [],
            "constraints": [],
            "preferences": {},
            "created_at": export["profile"]["created_at"],
            "updated_at": export["profile"]["updated_at"],
        }
        assert export["subscription"] is None, "a free account has no grant to export"
        for collection in (
            "onboarding_sessions",
            "analyses",
            "conversations",
            "memories",
            "events",
            "week_plans",
            "usage_counters",
        ):
            assert export[collection] == [], collection

    async def test_the_response_is_offered_as_a_file(self, api_client: AsyncClient):
        headers, _ = await authenticate(api_client, "ada@example.com")

        response = await api_client.get(EXPORT, headers=headers)

        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment; filename=")
        assert disposition.endswith('.json"')
        assert response.headers["content-type"].startswith("application/json")


class TestAPopulatedAccount:
    async def test_everything_the_user_can_see_elsewhere_is_in_the_export(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers, _ = await authenticate(api_client, "ada@example.com")
        await use_every_feature(api_client, headers, llm_provider, db_session, label="ada")

        export = (await api_client.get(EXPORT, headers=headers)).json()["data"]

        profile = (await api_client.get("/api/v1/profile", headers=headers)).json()["data"]
        assert {key: export["profile"][key] for key in profile} == profile

        conversations = (await api_client.get("/api/v1/conversations", headers=headers)).json()
        assert {item["id"] for item in export["conversations"]} == {
            item["id"] for item in conversations["data"]
        }

        memories = (
            await api_client.get(
                "/api/v1/memories?include_superseded=true&include_archived=true", headers=headers
            )
        ).json()["data"]
        assert {item["id"] for item in export["memories"]} == {item["id"] for item in memories}

        analysis = (await api_client.get("/api/v1/analysis/latest", headers=headers)).json()
        assert [item["id"] for item in export["analyses"]] == [analysis["data"]["id"]]

        plan = (await api_client.get("/api/v1/plans/week/latest", headers=headers)).json()
        assert [item["id"] for item in export["week_plans"]] == [plan["data"]["id"]]

        status = (await api_client.get("/api/v1/onboarding/status", headers=headers)).json()
        assert [item["id"] for item in export["onboarding_sessions"]] == [
            status["data"]["session_id"]
        ]

    async def test_conversations_carry_every_message_in_order(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers, _ = await authenticate(api_client, "ada@example.com")
        await use_every_feature(api_client, headers, llm_provider, db_session, label="ada")

        export = (await api_client.get(EXPORT, headers=headers)).json()["data"]

        assert len(export["conversations"]) == 2
        for conversation in export["conversations"]:
            assert [m["role"] for m in conversation["messages"]] == ["user", "assistant"]
            assert conversation["message_count"] == 2
        assert export["conversations"][0]["messages"][0]["content"] == "ada message"
        assert export["conversations"][1]["messages"][0]["content"] == "ada follow-up"

    async def test_memories_include_every_status_and_their_history(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers, _ = await authenticate(api_client, "ada@example.com")
        await use_every_feature(api_client, headers, llm_provider, db_session, label="ada")

        export = (await api_client.get(EXPORT, headers=headers)).json()["data"]

        by_content = {item["content"]: item for item in export["memories"]}
        superseded = by_content["ada is staying in mobile development"]
        current = by_content["ada wants to move into AI engineering"]
        assert superseded["status"] == "superseded"
        assert superseded["superseded_by_id"] == current["id"]
        assert [v["change"] for v in superseded["versions"]] == ["created", "superseded"]
        assert [v["change"] for v in current["versions"]] == ["created"]
        assert superseded["conversation_id"] == export["conversations"][0]["id"]
        assert "embedding" not in current, "vectors are derived data, not the user's"

    async def test_events_plans_analyses_and_the_interview_are_exported_whole(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers, _ = await authenticate(api_client, "ada@example.com")
        await use_every_feature(api_client, headers, llm_provider, db_session, label="ada")

        export = (await api_client.get(EXPORT, headers=headers)).json()["data"]

        [event] = export["events"]
        assert (event["activity"], event["quantity"], event["unit"]) == ("study", 2.0, "hours")
        assert event["memory_id"] in {item["id"] for item in export["memories"]}

        [plan] = export["week_plans"]
        assert plan["summary"] == "Three runs."
        assert [block["title"] for block in plan["blocks"]] == ["Easy run"]
        assert plan["timezone"] == "Asia/Kolkata"

        [analysis] = export["analyses"]
        assert [claim["content"] for claim in analysis["claims"]] == ["Wants to run a marathon"]
        assert analysis["evidence_snapshot"]["goals"] == ["ada goal"]

        [session] = export["onboarding_sessions"]
        assert session["status"] == "in_progress"
        assert session["questions_asked"] >= 1
        assert session["state"]["asked_questions"], "the interview so far is part of the state"
        assert "Run a marathon" in json.dumps(session["state"]["collected"])

    async def test_the_subscription_and_its_counters_are_exported(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers, _ = await authenticate(api_client, "ada@example.com")
        await use_every_feature(api_client, headers, llm_provider, db_session, label="ada")

        export = (await api_client.get(EXPORT, headers=headers)).json()["data"]

        assert export["subscription"]["plan"] == "pro"
        assert export["subscription"]["source"] == "ada-grant"
        assert export["subscription"]["expires_at"] is not None
        assert export["usage_counters"] == [
            {
                "feature": "chat_message",
                "period_start": usage_period(datetime.now(tz=UTC)).starts_on.isoformat(),
                "used": 2,
            }
        ]


class TestWhatIsNeverExported:
    async def test_no_credential_leaves_the_server(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers, refresh_token = await authenticate(api_client, "ada@example.com")
        user = (await db_session.execute(select(User))).scalar_one()
        stored_tokens = (await db_session.execute(select(RefreshToken))).scalars().all()
        assert stored_tokens, "the login above should have issued a refresh token"

        response = await api_client.get(EXPORT, headers=headers)

        raw = response.text
        assert PASSWORD not in raw
        assert user.password_hash not in raw
        assert refresh_token not in raw
        for token in stored_tokens:
            assert token.token_hash not in raw
            assert str(token.family_id) not in raw
        assert headers["Authorization"].removeprefix("Bearer ") not in raw
        lowered = [text.lower() for text in walk_strings(response.json())]
        assert not [text for text in lowered if "password" in text or "token" in text]

    async def test_only_the_callers_own_rows_are_exported(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        ada, _ = await authenticate(api_client, "ada@example.com")
        grace, _ = await authenticate(api_client, "grace@example.com")
        await use_every_feature(api_client, ada, llm_provider, db_session, label="ada")
        await use_every_feature(api_client, grace, llm_provider, db_session, label="grace")

        response = await api_client.get(EXPORT, headers=ada)

        export = response.json()["data"]
        assert export["account"]["email"] == "ada@example.com"
        assert "grace" not in response.text
        # Every collection is populated for Ada, so an empty one would hide a
        # filter that dropped her rows along with Grace's.
        for collection in (
            "onboarding_sessions",
            "analyses",
            "conversations",
            "memories",
            "events",
            "week_plans",
            "usage_counters",
        ):
            assert export[collection], collection
        assert export["subscription"]["source"] == "ada-grant"

    async def test_a_deleted_conversations_memories_are_still_exported(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """Deleting a conversation keeps what was learned; the export reflects that."""
        headers, _ = await authenticate(api_client, "ada@example.com")
        await use_every_feature(api_client, headers, llm_provider, db_session, label="ada")
        export = (await api_client.get(EXPORT, headers=headers)).json()["data"]
        for conversation in export["conversations"]:
            await api_client.delete(f"/api/v1/conversations/{conversation['id']}", headers=headers)

        after = (await api_client.get(EXPORT, headers=headers)).json()["data"]

        assert after["conversations"] == []
        assert {m["id"] for m in after["memories"]} == {m["id"] for m in export["memories"]}
        assert all(m["conversation_id"] is None for m in after["memories"])


class TestEnvelope:
    async def test_an_unknown_user_id_in_a_valid_token_is_unauthenticated(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """A token for an account deleted since it was issued gets the standard 401."""
        headers, _ = await authenticate(api_client, "ada@example.com")
        await api_client.request(
            "DELETE", "/api/v1/auth/account", headers=headers, json={"password": PASSWORD}
        )

        response = await api_client.get(EXPORT, headers=headers)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"
        assert uuid.UUID(response.json()["meta"]["request_id"])


# --- Production hardening ----------------------------------------------------------


@asynccontextmanager
async def client_for(
    settings: Settings, **overrides: Any
) -> AsyncIterator[tuple[AsyncClient, Any]]:
    """A client on an app built with different settings; yields it and the app."""
    application = create_app(
        settings.model_copy(update=overrides),
        llm_provider=FakeLLMProvider(replies=["Noted."]),
        embedding_provider=FakeEmbeddingProvider(),
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            yield client, application
    finally:
        await application.state.database_engine.dispose()


# Captured before any test swaps it out, so stand-ins can delegate to it.
REAL_RENDER = account_routes._render


def connections_in_use(application: Any) -> int:
    return int(application.state.database_engine.sync_engine.pool.checkedout())


class BlockingRender:
    """Stands in for the renderer, holding each export until released.

    The renderer runs on a worker thread, so it waits on a thread event.
    """

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.connections_seen: list[int] = []
        self.application: Any = None

    def __call__(self, export: Any, request_id: str | None) -> bytes:
        if self.application is not None:
            self.connections_seen.append(connections_in_use(self.application))
        self.entered.set()
        assert self.release.wait(timeout=10), "the test never released the export"
        return REAL_RENDER(export, request_id)


async def wait_for(event: threading.Event) -> None:
    assert await asyncio.to_thread(event.wait, 5), "timed out waiting for the export"


class TestTheDatabaseIsShared:
    async def test_reads_are_one_read_only_repeatable_read_snapshot(
        self, api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        seen: list[tuple[str, str]] = []
        original = EventRepository.all_for_user

        async def spying(self: EventRepository, user_id: uuid.UUID) -> Any:
            row = await self._session.execute(
                text(
                    "SELECT current_setting('transaction_isolation'), "
                    "current_setting('transaction_read_only')"
                )
            )
            isolation, read_only = row.one()
            seen.append((isolation, read_only))
            return await original(self, user_id)

        monkeypatch.setattr(EventRepository, "all_for_user", spying)
        headers, _ = await authenticate(api_client, "ada@example.com")

        response = await api_client.get(EXPORT, headers=headers)

        assert response.status_code == 200
        assert seen == [("repeatable read", "on")]

    async def test_a_write_during_the_export_is_wholly_in_or_wholly_out(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
        database_engine: AsyncEngine,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A message stored between two of the export's reads must not half-appear."""
        headers, _ = await authenticate(api_client, "ada@example.com")
        await api_client.post("/api/v1/chat", headers=headers, json={"message": "First"})
        conversation_id = (await db_session.execute(select(Conversation.id))).scalar_one()
        original = ConversationRepository.all_for_user

        async def then_write(self: ConversationRepository, user_id: uuid.UUID) -> Any:
            conversations = await original(self, user_id)
            # Another request, committed after the conversations were read.
            async with create_session_factory(database_engine)() as other:
                other.add(
                    Message(
                        conversation_id=conversation_id,
                        position=2,
                        role=MessageRole.USER,
                        content="Sent mid-export",
                    )
                )
                await other.commit()
            return conversations

        monkeypatch.setattr(ConversationRepository, "all_for_user", then_write)

        export = (await api_client.get(EXPORT, headers=headers)).json()["data"]

        [conversation] = export["conversations"]
        contents = [message["content"] for message in conversation["messages"]]
        assert "Sent mid-export" not in contents
        assert len(contents) == conversation["message_count"] == 2

    async def test_the_connection_is_released_before_the_response_is_built(
        self, database_settings: Settings, clean_database: None, monkeypatch: pytest.MonkeyPatch
    ):
        """Encoding a large account takes seconds; a pooled connection must not wait on it."""
        render = BlockingRender()
        render.release.set()
        monkeypatch.setattr(account_routes, "_render", render)
        async with client_for(database_settings) as (client, application):
            render.application = application
            headers, _ = await authenticate(client, "ada@example.com")

            response = await client.get(EXPORT, headers=headers)

        assert response.status_code == 200
        assert render.connections_seen == [0]

    async def test_the_snapshot_settings_do_not_leak_into_the_next_request(
        self, database_settings: Settings, clean_database: None
    ):
        """With one pooled connection, the next request reuses the export's; it must write."""
        async with client_for(database_settings, database_pool_size=1, database_max_overflow=0) as (
            client,
            _,
        ):
            headers, _ = await authenticate(client, "ada@example.com")
            await client.get(EXPORT, headers=headers)

            response = await client.patch(
                "/api/v1/profile", headers=headers, json={"constraints": ["Evenings only"]}
            )

        assert response.status_code == 200

    async def test_embedding_vectors_are_never_read(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Parsing them was most of a large export's cost, and they are not exported."""
        headers, _ = await authenticate(api_client, "ada@example.com")
        await use_every_feature(api_client, headers, llm_provider, db_session, label="ada")
        statements: list[str] = []
        original = AsyncSession.stream_scalars

        async def recording(self: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
            statements.append(str(statement))
            return await original(self, statement, *args, **kwargs)

        monkeypatch.setattr(AsyncSession, "stream_scalars", recording)

        response = await api_client.get(EXPORT, headers=headers)

        assert response.status_code == 200
        [memories_query] = [s for s in statements if "FROM memories" in s]
        selected = memories_query.split(" FROM ")[0]
        assert not re.search(r"memories\.embedding\b(?!_model)", selected)
        assert "memories.search_vector" not in selected
        assert "memories.content" in selected, "the check must be looking at the right query"


class TestBurstsOfExports:
    async def test_exports_past_the_limit_queue_without_holding_a_connection(
        self, database_settings: Settings, clean_database: None, monkeypatch: pytest.MonkeyPatch
    ):
        render = BlockingRender()
        monkeypatch.setattr(account_routes, "_render", render)
        async with client_for(database_settings, export_max_concurrent=1) as (client, application):
            ada, _ = await authenticate(client, "ada@example.com")
            grace, _ = await authenticate(client, "grace@example.com")

            first = asyncio.create_task(client.get(EXPORT, headers=ada))
            await wait_for(render.entered)
            render.entered.clear()
            second = asyncio.create_task(client.get(EXPORT, headers=grace))
            await asyncio.sleep(0.3)

            assert not second.done(), "the second export should be waiting for a slot"
            assert connections_in_use(application) == 0, "a queued export must not hold one"
            render.release.set()
            responses = await asyncio.gather(first, second)

        assert [r.status_code for r in responses] == [200, 200]
        assert responses[1].json()["data"]["account"]["email"] == "grace@example.com"

    async def test_an_export_that_cannot_get_a_slot_in_time_is_refused_with_503(
        self, database_settings: Settings, clean_database: None, monkeypatch: pytest.MonkeyPatch
    ):
        render = BlockingRender()
        monkeypatch.setattr(account_routes, "_render", render)
        async with client_for(
            database_settings, export_max_concurrent=1, export_queue_timeout_seconds=0.2
        ) as (client, _):
            ada, _ = await authenticate(client, "ada@example.com")
            grace, _ = await authenticate(client, "grace@example.com")

            first = asyncio.create_task(client.get(EXPORT, headers=ada))
            await wait_for(render.entered)
            refused = await client.get(EXPORT, headers=grace)
            render.release.set()
            await first
            after = await client.get(EXPORT, headers=grace)

        assert refused.status_code == 503
        assert refused.json()["error"]["code"] == "EXPORT_UNAVAILABLE"
        assert refused.json()["meta"]["request_id"]
        assert "content-disposition" not in refused.headers
        assert after.status_code == 200, "the slot must be free again once the first finished"

    async def test_a_failed_export_gives_its_slot_back(
        self, database_settings: Settings, clean_database: None, monkeypatch: pytest.MonkeyPatch
    ):
        """A slot leaked on failure would, after a few failures, block every export."""
        calls = 0

        def fail_once(export: Any, request_id: str | None) -> bytes:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("rendering failed")
            return REAL_RENDER(export, request_id)

        monkeypatch.setattr(account_routes, "_render", fail_once)
        async with client_for(
            database_settings, export_max_concurrent=1, export_queue_timeout_seconds=1
        ) as (client, application):
            headers, _ = await authenticate(client, "ada@example.com")

            with pytest.raises(RuntimeError):
                # The test transport re-raises what the app's 500 handler logged.
                await client.get(EXPORT, headers=headers)
            retried = await client.get(EXPORT, headers=headers)
            connections = connections_in_use(application)

        assert retried.status_code == 200
        assert connections == 0


def _asyncpg_error(sqlstate: str) -> DBAPIError:
    """A database error as SQLAlchemy raises it from asyncpg, with its SQLSTATE."""
    original = Exception("from the server")
    original.sqlstate = sqlstate  # type: ignore[attr-defined]
    return DBAPIError("SELECT ...", None, original)


class TestDatabaseFailures:
    @pytest.mark.parametrize(
        "failure",
        [
            TimeoutError(),
            PoolTimeoutError("QueuePool limit reached"),
            ConnectionRefusedError(),
            ConnectionResetError(),
            _asyncpg_error("57014"),  # statement cancelled by a timeout
            _asyncpg_error("57P01"),  # server shutting down
            _asyncpg_error("08006"),  # connection failure
            _asyncpg_error("53300"),  # too many connections
            _asyncpg_error("40001"),  # serialization failure
        ],
        ids=lambda error: (
            type(error).__name__
            + (f"-{getattr(error.orig, 'sqlstate', '')}" if isinstance(error, DBAPIError) else "")
        ),
    )
    async def test_a_database_that_cannot_serve_the_export_is_a_503(
        self,
        database_settings: Settings,
        clean_database: None,
        monkeypatch: pytest.MonkeyPatch,
        failure: BaseException,
    ):
        async def failing(self: Any, user_id: uuid.UUID) -> Any:
            raise failure

        monkeypatch.setattr(MemoryRepository, "all_for_user", failing)
        async with client_for(database_settings, export_max_concurrent=1) as (client, app):
            headers, _ = await authenticate(client, "ada@example.com")

            response = await client.get(EXPORT, headers=headers)
            connections = connections_in_use(app)
            monkeypatch.undo()
            recovered = await client.get(EXPORT, headers=headers)

        assert response.status_code == 503
        assert response.json()["error"] == {
            "code": "EXPORT_UNAVAILABLE",
            "message": (
                "Your data export could not be prepared right now. Please try again shortly."
            ),
        }
        assert connections == 0
        assert recovered.status_code == 200, "neither the slot nor the connection leaked"

    @pytest.mark.parametrize(
        "fault",
        [_asyncpg_error("42P01"), _asyncpg_error("22P02"), ValueError("a bug")],
        ids=["undefined-table", "invalid-text", "value-error"],
    )
    async def test_a_fault_in_the_export_itself_is_not_disguised_as_unavailable(
        self, api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, fault: BaseException
    ):
        """A 503 invites retrying; for a bug, retrying changes nothing and hides it."""

        async def failing(self: Any, user_id: uuid.UUID) -> Any:
            raise fault

        monkeypatch.setattr(MemoryRepository, "all_for_user", failing)
        headers, _ = await authenticate(api_client, "ada@example.com")

        with pytest.raises(type(fault)):
            # The test transport re-raises what the app's 500 handler logged.
            await api_client.get(EXPORT, headers=headers)


class TestUnusualData:
    async def test_stored_json_of_any_shape_is_exported_as_it_is(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """Older rows may predate today's shapes; the export must hand them back, not fail."""
        headers, _ = await authenticate(api_client, "ada@example.com")
        user_id = (await db_session.execute(select(User.id))).scalar_one()
        db_session.add_all(
            [
                OnboardingSession(
                    user_id=user_id,
                    status=OnboardingStatus.ABANDONED,
                    questions_asked=1,
                    overall_confidence=0.0,
                    state=cast(Any, ["an", "old", "shape", None, 3]),
                ),
                Analysis(
                    user_id=user_id,
                    evidence_snapshot=cast(Any, []),
                    claims=cast(Any, {"legacy": True}),
                    prompt_name="analysis.initial",
                    prompt_version="0",
                ),
                WeekPlan(
                    user_id=user_id,
                    starts_on=datetime.now(tz=UTC).date(),
                    timezone="UTC",
                    summary="Legacy",
                    blocks=cast(Any, "not a list"),
                    evidence_snapshot=cast(Any, None),
                    prompt_name="planning.week",
                    prompt_version="0",
                ),
            ]
        )
        await db_session.commit()

        response = await api_client.get(EXPORT, headers=headers)

        assert response.status_code == 200
        export = response.json()["data"]
        assert export["onboarding_sessions"][0]["state"] == ["an", "old", "shape", None, 3]
        assert export["analyses"][0]["claims"] == {"legacy": True}
        assert export["week_plans"][0]["blocks"] == "not a list"
        assert export["week_plans"][0]["evidence_snapshot"] is None

    async def test_non_finite_numbers_still_produce_valid_json(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """Postgres floats can hold NaN and Infinity, which JSON cannot express."""
        headers, _ = await authenticate(api_client, "ada@example.com")
        user_id = (await db_session.execute(select(User.id))).scalar_one()
        db_session.add_all(
            [
                Memory(
                    user_id=user_id,
                    content="An odd one",
                    memory_type=MemoryType.FACT,
                    status=MemoryStatus.ACTIVE,
                    durability=MemoryDurability.LONG_TERM,
                    source=MemorySource.CONVERSATION,
                    importance=float("nan"),
                    confidence=float("inf"),
                ),
                Event(
                    user_id=user_id,
                    activity="study",
                    description="Studied",
                    occurred_at=datetime.now(tz=UTC),
                    quantity=float("-inf"),
                    unit="hours",
                ),
            ]
        )
        await db_session.commit()

        response = await api_client.get(EXPORT, headers=headers)

        assert response.status_code == 200

        def refuse(token: str) -> None:
            raise AssertionError(f"{token} is not valid JSON")

        export = json.loads(response.text, parse_constant=refuse)["data"]
        assert export["memories"][0]["importance"] is None
        assert export["memories"][0]["confidence"] is None
        assert export["events"][0]["quantity"] is None

    async def test_awkward_text_survives_exactly(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        awkward = 'Quotes " and \\ backslash, </script>, emoji 🧠, עברית, zero\u200bwidth, tab\tend'
        headers, _ = await authenticate(api_client, "ada@example.com")
        await api_client.patch(
            "/api/v1/profile",
            headers=headers,
            json={
                "goals": [{"name": awkward, "priority": "low"}],
                "preferences": {awkward: awkward},
            },
        )
        await api_client.post("/api/v1/chat", headers=headers, json={"message": awkward})

        export = (await api_client.get(EXPORT, headers=headers)).json()["data"]

        assert export["profile"]["goals"][0]["name"] == awkward
        assert export["profile"]["preferences"] == {awkward: awkward}
        assert export["conversations"][0]["messages"][0]["content"] == awkward

    async def test_an_account_without_a_profile_exports_it_as_null(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers, _ = await authenticate(api_client, "ada@example.com")
        await db_session.execute(delete(Profile))
        await db_session.commit()

        response = await api_client.get(EXPORT, headers=headers)

        assert response.status_code == 200
        assert response.json()["data"]["profile"] is None

    async def test_the_body_matches_the_documented_schema(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """The route returns encoded bytes, so FastAPI no longer checks them; this does."""
        headers, _ = await authenticate(api_client, "ada@example.com")
        await use_every_feature(api_client, headers, llm_provider, db_session, label="ada")

        response = await api_client.get(EXPORT, headers=headers)

        parsed = DataResponse[AccountExportResponse].model_validate_json(response.content)
        assert parsed.data.account.email == "ada@example.com"
        assert response.json()["meta"]["request_id"] == response.headers["x-request-id"]


class TestHttpSurface:
    async def test_the_export_is_never_stored_by_a_cache(self, api_client: AsyncClient):
        headers, _ = await authenticate(api_client, "ada@example.com")

        response = await api_client.get(EXPORT, headers=headers)

        assert response.headers["cache-control"] == "no-store"

    async def test_the_filename_holds_only_the_date(self, api_client: AsyncClient):
        """Nothing the user controls reaches a response header."""
        headers, _ = await authenticate(api_client, "ada@example.com")

        response = await api_client.get(EXPORT, headers=headers)

        assert re.fullmatch(
            r'attachment; filename="personal-intelligence-export-\d{4}-\d{2}-\d{2}\.json"',
            response.headers["content-disposition"],
        )

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    async def test_other_methods_are_refused_in_the_standard_envelope(
        self, api_client: AsyncClient, method: str
    ):
        headers, _ = await authenticate(api_client, "ada@example.com")

        response = await api_client.request(method, EXPORT, headers=headers)

        assert response.status_code == 405
        assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"

    async def test_a_deactivated_account_is_refused_in_the_standard_envelope(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers, _ = await authenticate(api_client, "ada@example.com")
        await db_session.execute(update(User).values(is_active=False))
        await db_session.commit()

        response = await api_client.get(EXPORT, headers=headers)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INACTIVE_USER"
