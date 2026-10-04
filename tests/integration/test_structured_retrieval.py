from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory.types import (
    MemoryCandidate,
    MemoryDurability,
    MemoryExtraction,
    MemoryType,
)
from app.domain.retrieval.plan import QueryPlan, RetrievalNeed
from app.domain.temporal.ranges import TimeRangeSpec
from app.infrastructure.database.models import Event
from tests.fakes import FakeLLMProvider

pytestmark = pytest.mark.integration

PASSWORD = "a-sufficiently-long-password"
EXACT_HEADING = "Exact figures, computed from the user's records"
TIMELINE_HEADING = "What the user did during"


def event_candidate(
    description: str,
    activity: str,
    *,
    quantity: float | None = None,
    unit: str | None = None,
    days_ago: int = 0,
) -> MemoryCandidate:
    return MemoryCandidate(
        content=description,
        memory_type=MemoryType.EVENT,
        durability=MemoryDurability.SHORT_TERM,
        importance=0.6,
        confidence=0.9,
        activity=activity,
        quantity=quantity,
        unit=unit,
        days_ago=days_ago,
    )


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(replies=["Understood."])


async def authenticate(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    access_token: str = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


async def record(
    client: AsyncClient,
    provider: FakeLLMProvider,
    headers: dict[str, str],
    *candidates: MemoryCandidate,
) -> None:
    provider.queue_memory_extractions(MemoryExtraction(candidates=list(candidates)))
    provider.queue_plans(QueryPlan(needs=[RetrievalNeed.SEMANTIC]))
    await client.post("/api/v1/chat", headers=headers, json={"message": "Logging what I did."})
    provider.queue_memory_extractions(MemoryExtraction())


async def ask(
    client: AsyncClient,
    provider: FakeLLMProvider,
    headers: dict[str, str],
    question: str,
    plan: QueryPlan,
) -> str:
    provider.queue_plans(plan)
    await client.post("/api/v1/chat", headers=headers, json={"message": question})
    return provider.prompts_named("conversation.reply")[-1].user


def section(prompt: str, heading: str) -> str:
    if heading not in prompt:
        return ""
    remainder = prompt[prompt.index(heading) :]
    for marker in (
        "Things you remember",
        TIMELINE_HEADING,
        "Earlier in this",
        "The message to answer:",
    ):
        index = remainder.find(marker, len(heading))
        if index != -1:
            remainder = remainder[:index]
    return remainder


class TestEventRecording:
    async def test_a_countable_event_is_stored_as_a_row(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")

        await record(
            api_client,
            llm_provider,
            headers,
            event_candidate("Studied algorithms for 2 hours", "study", quantity=2, unit="hours"),
        )

        event = (await db_session.execute(select(Event))).scalar_one()
        assert event.activity == "study"
        assert event.quantity == 2.0
        assert event.unit == "hours"

    async def test_a_memory_that_is_not_an_event_records_no_row(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")

        await record(
            api_client,
            llm_provider,
            headers,
            MemoryCandidate(
                content="I want to move into AI engineering",
                memory_type=MemoryType.GOAL,
                durability=MemoryDurability.LONG_TERM,
                importance=0.9,
                confidence=0.9,
            ),
        )

        assert (await db_session.execute(select(Event))).scalars().all() == []

    async def test_a_day_offset_places_the_event_in_the_past(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """The model supplies an offset, never a date."""
        headers = await authenticate(api_client, "ada@example.com")

        await record(
            api_client,
            llm_provider,
            headers,
            event_candidate("Ran 5km", "running", quantity=5, unit="km", days_ago=3),
        )

        event = (await db_session.execute(select(Event))).scalar_one()
        age = datetime.now(tz=UTC) - event.occurred_at
        assert timedelta(days=2) < age < timedelta(days=4)


class TestRepeatedOccurrences:
    async def test_the_same_activity_twice_counts_twice(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """Deduplication is about beliefs, not moments. Studying on Monday and
        again on Wednesday is two occurrences however alike the wording, and
        collapsing them would silently undercount every total."""
        headers = await authenticate(api_client, "ada@example.com")

        await record(
            api_client,
            llm_provider,
            headers,
            event_candidate("Studied algorithms", "study", quantity=2, unit="hours", days_ago=1),
        )
        await record(
            api_client,
            llm_provider,
            headers,
            event_candidate("Studied algorithms", "study", quantity=3, unit="hours", days_ago=2),
        )

        events = (await db_session.execute(select(Event))).scalars().all()
        assert len(events) == 2
        assert sorted(event.quantity or 0 for event in events) == [2.0, 3.0]

    async def test_a_repeated_belief_is_still_only_remembered_once(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """The other half of the rule: the memory is not duplicated."""
        headers = await authenticate(api_client, "ada@example.com")

        for _ in range(2):
            await record(
                api_client,
                llm_provider,
                headers,
                event_candidate("Studied algorithms", "study", quantity=2, unit="hours"),
            )

        memories = (await api_client.get("/api/v1/memories", headers=headers)).json()
        assert memories["meta"]["total_items"] == 1

    async def test_repeated_occurrences_reach_the_total(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        for quantity in (2, 3):
            await record(
                api_client,
                llm_provider,
                headers,
                event_candidate(
                    "Studied algorithms", "study", quantity=quantity, unit="hours", days_ago=1
                ),
            )

        prompt = await ask(
            api_client,
            llm_provider,
            headers,
            "How much did I study?",
            QueryPlan(needs=[RetrievalNeed.TOTALS], activities=["study"]),
        )

        figures = section(prompt, EXACT_HEADING)
        assert "2 occurrence(s)" in figures
        assert "5 hours" in figures


class TestExactTotals:
    async def _log_a_week(
        self, client: AsyncClient, provider: FakeLLMProvider, headers: dict[str, str]
    ) -> None:
        await record(
            client,
            provider,
            headers,
            event_candidate("Studied 2 hours", "study", quantity=2, unit="hours", days_ago=1),
            event_candidate("Studied 1.5 hours", "study", quantity=1.5, unit="hours", days_ago=2),
            event_candidate("Studied 3 hours", "study", quantity=3, unit="hours", days_ago=3),
        )

    async def test_the_total_reaching_the_prompt_is_computed_not_estimated(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """The central rule of the phase: the arithmetic is the database's."""
        headers = await authenticate(api_client, "ada@example.com")
        await self._log_a_week(api_client, llm_provider, headers)

        prompt = await ask(
            api_client,
            llm_provider,
            headers,
            "How many hours did I study in the last week?",
            QueryPlan(
                needs=[RetrievalNeed.TOTALS],
                time_range=TimeRangeSpec.LAST_7_DAYS,
                activities=["study"],
            ),
        )

        figures = section(prompt, EXACT_HEADING)
        assert "6.5 hours" in figures
        assert "3 occurrence(s)" in figures

    async def test_the_prompt_forbids_recomputing_the_figure(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """The figure is settled; the model's job is to explain it, not redo it."""
        headers = await authenticate(api_client, "ada@example.com")
        await self._log_a_week(api_client, llm_provider, headers)

        await ask(
            api_client,
            llm_provider,
            headers,
            "How many hours did I study?",
            QueryPlan(needs=[RetrievalNeed.TOTALS], time_range=TimeRangeSpec.LAST_7_DAYS),
        )

        instructions = llm_provider.prompts_named("conversation.reply")[-1].system
        assert "do not recompute them" in instructions
        assert "do not estimate a number of your own" in instructions

    async def test_the_period_the_figures_cover_is_stated(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await self._log_a_week(api_client, llm_provider, headers)

        prompt = await ask(
            api_client,
            llm_provider,
            headers,
            "How much did I study?",
            QueryPlan(needs=[RetrievalNeed.TOTALS], time_range=TimeRangeSpec.LAST_7_DAYS),
        )

        assert " to " in section(prompt, EXACT_HEADING)

    async def test_events_outside_the_period_are_excluded(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await record(
            api_client,
            llm_provider,
            headers,
            event_candidate("Studied today", "study", quantity=2, unit="hours", days_ago=0),
            event_candidate("Studied ages ago", "study", quantity=50, unit="hours", days_ago=200),
        )

        prompt = await ask(
            api_client,
            llm_provider,
            headers,
            "How much did I study today?",
            QueryPlan(needs=[RetrievalNeed.TOTALS], time_range=TimeRangeSpec.TODAY),
        )

        figures = section(prompt, EXACT_HEADING)
        assert "2 hours" in figures
        assert "52" not in figures

    async def test_different_units_are_never_added_together(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """Adding hours to kilometres yields a number with no meaning."""
        headers = await authenticate(api_client, "ada@example.com")
        await record(
            api_client,
            llm_provider,
            headers,
            event_candidate("Ran 5km", "running", quantity=5, unit="km"),
            event_candidate("Ran for 1 hour", "running", quantity=1, unit="hours"),
        )

        prompt = await ask(
            api_client,
            llm_provider,
            headers,
            "How much running have I done?",
            QueryPlan(needs=[RetrievalNeed.TOTALS], activities=["running"]),
        )

        figures = section(prompt, EXACT_HEADING)
        assert "5 km" in figures
        assert "1 hours" in figures
        assert "6" not in figures

    async def test_occurrences_without_an_amount_are_reported_honestly(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """ "Three sessions, two of them timed" beats a total that hides the gap."""
        headers = await authenticate(api_client, "ada@example.com")
        await record(
            api_client,
            llm_provider,
            headers,
            event_candidate("Studied 2 hours", "study", quantity=2, unit="hours"),
            event_candidate("Studied, did not time it", "study"),
        )

        prompt = await ask(
            api_client,
            llm_provider,
            headers,
            "How much have I studied?",
            QueryPlan(needs=[RetrievalNeed.TOTALS], activities=["study"]),
        )

        assert "without a recorded amount" in section(prompt, EXACT_HEADING)

    async def test_no_matching_events_produce_no_figures_rather_than_a_zero(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")

        prompt = await ask(
            api_client,
            llm_provider,
            headers,
            "How much did I study last week?",
            QueryPlan(needs=[RetrievalNeed.TOTALS], time_range=TimeRangeSpec.LAST_WEEK),
        )

        assert EXACT_HEADING not in prompt


class TestTimeline:
    async def test_a_timeline_reaches_the_prompt_in_order(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await record(
            api_client,
            llm_provider,
            headers,
            event_candidate("Started the course", "study", days_ago=5),
            event_candidate("Finished module two", "study", days_ago=1),
        )

        prompt = await ask(
            api_client,
            llm_provider,
            headers,
            "What have I been up to?",
            QueryPlan(needs=[RetrievalNeed.TIMELINE], time_range=TimeRangeSpec.LAST_7_DAYS),
        )

        timeline = section(prompt, TIMELINE_HEADING)
        assert timeline.index("Started the course") < timeline.index("Finished module two")

    async def test_a_question_needing_no_timeline_gets_none(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await record(
            api_client, llm_provider, headers, event_candidate("Studied", "study", days_ago=1)
        )

        prompt = await ask(
            api_client,
            llm_provider,
            headers,
            "What is my goal again?",
            QueryPlan(needs=[RetrievalNeed.SEMANTIC]),
        )

        assert TIMELINE_HEADING not in prompt


class TestUserIsolation:
    async def test_totals_never_include_another_users_events(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        ada = await authenticate(api_client, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")
        await record(
            api_client,
            llm_provider,
            ada,
            event_candidate("Studied 40 hours", "study", quantity=40, unit="hours"),
        )
        await record(
            api_client,
            llm_provider,
            grace,
            event_candidate("Studied 1 hour", "study", quantity=1, unit="hours"),
        )

        prompt = await ask(
            api_client,
            llm_provider,
            grace,
            "How much did I study?",
            QueryPlan(needs=[RetrievalNeed.TOTALS], activities=["study"]),
        )

        figures = section(prompt, EXACT_HEADING)
        assert "1 hours" in figures
        assert "40" not in figures
        assert "41" not in figures
