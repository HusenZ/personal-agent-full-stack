"""The event endpoints living alongside extraction, without disturbing it.

Extraction writes events in the background; users now read, correct and delete
them through the API. These tests interleave the two through the real chat
path and the real worker, then check the data as a whole:

- extraction still records, reinforces, dates and deletes exactly as before;
- every row the API writes is one extraction could have written;
- the list endpoint, the SQL totals and the account export agree, for every
  period the chat can ask about, in the user's own time zone.
"""

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory.types import MemoryExtraction
from app.domain.retrieval.plan import QueryPlan, RetrievalNeed
from app.domain.temporal.ranges import TimeRangeSpec, local_date, resolve_range
from app.infrastructure.database.models import BackgroundJob, Event, Memory
from tests.fakes import FakeLLMProvider
from tests.integration.conftest import drain_jobs
from tests.integration.events_support import (
    EVENTS,
    MEMORIES,
    Totals,
    User,
    event_candidate,
    event_row,
    listed,
    local_midday,
    local_today,
    patch,
    record,
    register,
    seed,
    totals,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(replies=["Understood."])


async def all_events(client: AsyncClient, user: User, query: str = "") -> list[dict[str, Any]]:
    """Every page of a listing, so nothing is missed past the first."""
    events: list[dict[str, Any]] = []
    page = 1
    while True:
        body = await listed(client, user, f"?page_size=100&page={page}{query}")
        events += body["data"]
        if not body["meta"]["has_next"]:
            return events
        page += 1


def aggregate(events: list[dict[str, Any]]) -> Totals:
    """The totals SQL computes, worked out independently from listed events."""
    groups: dict[tuple[str, str | None], list[float | None]] = defaultdict(list)
    for event in events:
        groups[(event["activity"], event["unit"])].append(event["quantity"])
    result: Totals = {}
    for key, quantities in groups.items():
        amounts = [quantity for quantity in quantities if quantity is not None]
        result[key] = (
            len(quantities),
            float(sum(amounts)) if amounts else None,
            len(quantities) - len(amounts),
        )
    return result


async def assert_list_agrees_with_totals(
    client: AsyncClient, session: AsyncSession, user: User, zone: str
) -> None:
    """For every period the chat can name, the list and the SQL totals see the same events.

    The period is resolved exactly as the chat resolves it, then expressed as
    the inclusive local dates the list endpoint takes. Any disagreement about
    which day an event is on, or whether it exists, shows up here.
    """
    now = datetime.now(tz=UTC)
    for spec in TimeRangeSpec:
        period = resolve_range(spec, now, ZoneInfo(zone))
        query = ""
        if period.start is not None:
            query += f"&start_date={local_date(period.start, ZoneInfo(zone))}"
        if period.end is not None:
            last_day = local_date(period.end - timedelta(microseconds=1), ZoneInfo(zone))
            query += f"&end_date={last_day}"
        expected = aggregate(await all_events(client, user, query))
        assert await totals(session, user, spec, zone) == expected, spec


async def assert_integrity(session: AsyncSession) -> None:
    """Invariants every row must hold, however it was written."""
    rows = (
        await session.execute(
            text(
                """
                SELECT e.id, e.user_id, e.activity, e.unit, e.description,
                       e.memory_id, m.user_id AS memory_owner,
                       e.conversation_id, c.user_id AS conversation_owner
                FROM events e
                LEFT JOIN memories m ON m.id = e.memory_id
                LEFT JOIN conversations c ON c.id = e.conversation_id
                """
            )
        )
    ).all()
    assert rows, "nothing to check"
    for row in rows:
        # A link never crosses accounts, and never dangles.
        if row.memory_id is not None:
            assert row.memory_owner == row.user_id, row
        if row.conversation_id is not None:
            assert row.conversation_owner == row.user_id, row
        # Stored normalised, so grouping and filtering see one label per activity.
        assert row.activity == row.activity.strip().lower(), row
        assert row.unit is None or row.unit == row.unit.strip().lower(), row
        assert row.description == row.description.strip() and row.description, row


async def assert_export_matches_list(client: AsyncClient, user: User) -> None:
    export = (await client.get("/api/v1/account/export", headers=user.headers)).json()["data"]
    fields = ("id", "activity", "description", "quantity", "unit", "memory_id", "occurred_at")

    def normalise(event: dict[str, Any]) -> tuple[Any, ...]:
        values = {field: event[field] for field in fields}
        values["occurred_at"] = datetime.fromisoformat(values["occurred_at"])
        return tuple(values[field] for field in fields)

    assert sorted(map(normalise, export["events"])) == sorted(
        map(normalise, await all_events(client, user))
    )


class TestExtractionStillWorks:
    @pytest.mark.parametrize("zone", ["UTC", "Pacific/Kiritimati", "America/Los_Angeles"])
    async def test_an_extracted_event_is_listed_on_the_day_it_was_placed(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, zone: str
    ):
        user = await register(api_client, timezone=zone)
        await record(
            api_client,
            llm_provider,
            user,
            event_candidate("Studied algorithms", quantity=2, unit="hours", days_ago=2),
        )

        [event] = await all_events(api_client, user)

        day = local_today(zone) - timedelta(days=2)
        assert event["date"] == day.isoformat()
        assert datetime.fromisoformat(event["occurred_at"]) == local_midday(day, zone)

    async def test_redating_an_extracted_event_to_its_own_day_moves_nothing(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """Extraction and correction place a day identically, to the microsecond."""
        zone = "Pacific/Kiritimati"
        user = await register(api_client, timezone=zone)
        await record(
            api_client,
            llm_provider,
            user,
            event_candidate("Studied algorithms", quantity=2, unit="hours", days_ago=3),
        )
        [event] = await all_events(api_client, user)
        before = await event_row(db_session, event["id"])

        response = await patch(
            api_client,
            user,
            event["id"],
            {
                "date": event["date"],
                "activity": event["activity"],
                "unit": event["unit"],
                "quantity": event["quantity"],
                "description": event["description"],
            },
        )

        assert response.status_code == 200, response.text
        assert await event_row(db_session, event["id"]) == before

    async def test_a_restatement_after_edits_and_deletes_still_reinforces_and_records(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """Extraction only ever inserts: it neither undoes an edit nor restores a deletion."""
        user = await register(api_client)
        for quantity, days_ago in ((2, 3), (3, 2)):
            await record(
                api_client,
                llm_provider,
                user,
                event_candidate(
                    "Studied algorithms", quantity=quantity, unit="hours", days_ago=days_ago
                ),
            )
        newer, older = await all_events(api_client, user)
        await patch(api_client, user, newer["id"], {"quantity": 4, "description": "Corrected"})
        await api_client.delete(f"{EVENTS}/{older['id']}", headers=user.headers)
        edited = await event_row(db_session, newer["id"])

        await record(
            api_client,
            llm_provider,
            user,
            event_candidate("Studied algorithms", quantity=1, unit="hours", days_ago=0),
        )

        events = await all_events(api_client, user)
        assert len(events) == 2
        assert older["id"] not in {event["id"] for event in events}
        assert await event_row(db_session, newer["id"]) == edited
        # Still one memory: the restatement reinforced the one the edited event belongs to.
        assert {event["memory_id"] for event in events} == {newer["memory_id"]}
        memories = (await api_client.get(MEMORIES, headers=user.headers)).json()
        assert memories["meta"]["total_items"] == 1
        assert await totals(db_session, user) == {("study", "hours"): (2, 5.0, 0)}

    async def test_deleting_a_memory_still_takes_every_event_it_owns(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """Memory deletion is unchanged: edited and redated occurrences go with it."""
        user = await register(api_client)
        for days_ago in (1, 2):
            await record(
                api_client,
                llm_provider,
                user,
                event_candidate("Studied algorithms", quantity=2, unit="hours", days_ago=days_ago),
            )
        await record(
            api_client,
            llm_provider,
            user,
            event_candidate("Ran by the river", "running", quantity=5, unit="km", days_ago=1),
        )
        study = [
            event for event in await all_events(api_client, user) if event["activity"] == "study"
        ]
        await patch(api_client, user, study[0]["id"], {"activity": "reading", "quantity": 9})
        await patch(
            api_client,
            user,
            study[1]["id"],
            {"date": (local_today("UTC") - timedelta(days=30)).isoformat()},
        )

        response = await api_client.delete(
            f"{MEMORIES}/{study[0]['memory_id']}", headers=user.headers
        )

        assert response.status_code == 204
        assert [event["activity"] for event in await all_events(api_client, user)] == ["running"]

    async def test_editing_a_memory_still_leaves_its_events_as_they_are(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """The memory service's behaviour is pinned, not changed, by the event API."""
        user = await register(api_client)
        await record(
            api_client,
            llm_provider,
            user,
            event_candidate("Studied algorithms", quantity=2, unit="hours", days_ago=1),
        )
        [event] = await all_events(api_client, user)
        await patch(api_client, user, event["id"], {"description": "Studied graphs"})
        before = await event_row(db_session, event["id"])

        await api_client.patch(
            f"{MEMORIES}/{event['memory_id']}",
            headers=user.headers,
            json={"content": "Studied algorithms and graphs", "status": "archived"},
        )

        assert await event_row(db_session, event["id"]) == before

    async def test_an_edit_between_queueing_and_extraction_survives_the_job(
        self,
        plain_client: AsyncClient,
        api_app: FastAPI,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
    ):
        """A job queued before an edit runs after it, and only adds its own event."""
        user = await register(plain_client)
        restated = event_candidate("Studied algorithms", quantity=2, unit="hours", days_ago=1)
        llm_provider.queue_memory_extractions(
            MemoryExtraction(candidates=[restated]), MemoryExtraction(candidates=[restated])
        )
        llm_provider.queue_plans(
            QueryPlan(needs=[RetrievalNeed.SEMANTIC]), QueryPlan(needs=[RetrievalNeed.SEMANTIC])
        )
        await plain_client.post("/api/v1/chat", headers=user.headers, json={"message": "One."})
        await drain_jobs(api_app)
        [first] = await all_events(plain_client, user)

        await plain_client.post("/api/v1/chat", headers=user.headers, json={"message": "Two."})
        await patch(plain_client, user, first["id"], {"quantity": 7, "unit": "Minutes"})
        edited = await event_row(db_session, first["id"])
        assert await drain_jobs(api_app) == 1

        events = await all_events(plain_client, user)
        assert len(events) == 2
        assert await event_row(db_session, first["id"]) == edited
        # Exactly once: running the worker again records nothing more.
        assert await drain_jobs(api_app) == 0
        db_session.expire_all()
        assert await db_session.scalar(select(func.count()).select_from(Event)) == 2
        assert await db_session.scalar(select(func.count()).select_from(BackgroundJob)) == 2


class TestStatesOnlyExtractionProduces:
    """Extraction is laxer than the API. Its rows must still be served and fixable."""

    @pytest.mark.parametrize(
        ("quantity", "unit"),
        [
            # An amount without a unit, and a unit without an amount.
            (2.0, None),
            (None, "hours"),
            # Beyond what an edit may set, but a number extraction accepts.
            (5_000_000.0, "steps"),
        ],
    )
    async def test_such_a_row_is_listed_faithfully_and_stays_editable(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
        quantity: float | None,
        unit: str | None,
    ):
        user = await register(api_client)
        await record(
            api_client,
            llm_provider,
            user,
            event_candidate("Walked around", "walking", quantity=quantity, unit=unit),
        )
        [event] = await all_events(api_client, user)
        assert (event["quantity"], event["unit"]) == (quantity, unit)

        described = await patch(api_client, user, event["id"], {"description": "Walked"})
        redated = await patch(api_client, user, event["id"], {"date": event["date"]})

        assert described.status_code == 200, described.text
        assert redated.status_code == 200, redated.text
        after = redated.json()["data"]
        assert (after["quantity"], after["unit"]) == (quantity, unit)


class TestDataIntegrity:
    async def test_a_mixed_history_leaves_every_view_of_the_data_consistent(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
    ):
        """Extraction, edits, deletions and refused attempts, across two users and zones."""
        ada_zone, grace_zone = "Pacific/Kiritimati", "America/Los_Angeles"
        ada = await register(api_client, "ada@example.com", timezone=ada_zone)
        grace = await register(api_client, "grace@example.com", timezone=grace_zone)

        for candidate in (
            event_candidate("Studied algorithms", quantity=2, unit="hours", days_ago=0),
            event_candidate("Studied algorithms", quantity=3, unit="hours", days_ago=1),
            event_candidate("Ran by the river", "running", quantity=5, unit="km", days_ago=3),
            event_candidate("Read a novel", "reading", days_ago=8),
            event_candidate("Practised piano scales", "piano", quantity=2, days_ago=2),
            event_candidate("Swam laps", "Swimming ", quantity=0.5, unit=" Hours", days_ago=40),
        ):
            await record(api_client, llm_provider, ada, candidate)
        await record(
            api_client,
            llm_provider,
            grace,
            event_candidate("Studied algorithms", quantity=1, unit="hours", days_ago=0),
        )
        # Something outside extraction's reach, in the same table.
        await seed(db_session, grace, datetime.now(tz=UTC), "study", quantity=1.5, unit="hours")

        by_activity: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in await all_events(api_client, ada):
            by_activity[event["activity"]].append(event)
        today_study, yesterday_study = by_activity["study"]
        await patch(api_client, ada, yesterday_study["id"], {"quantity": 3.5})
        await patch(
            api_client,
            ada,
            by_activity["running"][0]["id"],
            {"date": (local_today(ada_zone) - timedelta(days=10)).isoformat()},
        )
        await patch(api_client, ada, by_activity["reading"][0]["id"], {"activity": " Reading "})
        await patch(api_client, ada, by_activity["piano"][0]["id"], {"unit": "Hours"})
        await api_client.delete(f"{EVENTS}/{today_study['id']}", headers=ada.headers)

        # Refused: Grace cannot touch Ada's rows, and a bad edit applies nothing.
        for event_id in (yesterday_study["id"], by_activity["running"][0]["id"]):
            assert (await patch(api_client, grace, event_id, {"quantity": 99})).status_code == 404
            deleted = await api_client.delete(f"{EVENTS}/{event_id}", headers=grace.headers)
            assert deleted.status_code == 404
        assert (
            await patch(api_client, ada, yesterday_study["id"], {"unit": None})
        ).status_code == 422

        # Extraction carries on: a restatement reinforces and records again.
        await record(
            api_client,
            llm_provider,
            ada,
            event_candidate("Studied algorithms", quantity=1.5, unit="hours", days_ago=0),
        )

        await assert_integrity(db_session)
        await assert_list_agrees_with_totals(api_client, db_session, ada, ada_zone)
        await assert_list_agrees_with_totals(api_client, db_session, grace, grace_zone)
        await assert_export_matches_list(api_client, ada)
        await assert_export_matches_list(api_client, grace)

        assert await totals(db_session, ada, zone=ada_zone) == {
            ("study", "hours"): (2, 5.0, 0),
            ("running", "km"): (1, 5.0, 0),
            ("reading", None): (1, None, 1),
            ("piano", "hours"): (1, 2.0, 0),
            ("swimming", "hours"): (1, 0.5, 0),
        }
        assert await totals(db_session, grace, zone=grace_zone) == {("study", "hours"): (2, 2.5, 0)}
        # Every memory that was extracted survives event deletions.
        db_session.expire_all()
        ada_memories = await db_session.scalar(
            select(func.count()).select_from(Memory).where(Memory.user_id == ada.id)
        )
        assert ada_memories == 5

    async def test_every_extracted_or_edited_event_sits_at_local_midday(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """The storage convention every range query relies on, checked across paths."""
        zone = "Etc/GMT+12"
        user = await register(api_client, timezone=zone)
        for days_ago in (0, 1, 5):
            await record(
                api_client,
                llm_provider,
                user,
                event_candidate(
                    f"Studied chapter {days_ago}", quantity=1, unit="hours", days_ago=days_ago
                ),
            )
        events = await all_events(api_client, user)
        await patch(
            api_client,
            user,
            events[0]["id"],
            {"date": (local_today(zone) - timedelta(days=9)).isoformat()},
        )

        db_session.expire_all()
        stored = (await db_session.execute(select(Event.occurred_at))).scalars().all()
        assert len(stored) == 3
        for moment in stored:
            local = moment.astimezone(ZoneInfo(zone))
            assert (local.hour, local.minute, local.second, local.microsecond) == (12, 0, 0, 0)
        await assert_list_agrees_with_totals(api_client, db_session, user, zone)

    async def test_totals_for_an_unknown_event_id_are_never_affected(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        user = await register(api_client)
        await record(
            api_client,
            llm_provider,
            user,
            event_candidate("Studied algorithms", quantity=2, unit="hours", days_ago=1),
        )
        before = await totals(db_session, user)

        for _ in range(3):
            await patch(api_client, user, uuid.uuid4(), {"quantity": 50})
            await api_client.delete(f"{EVENTS}/{uuid.uuid4()}", headers=user.headers)

        assert await totals(db_session, user) == before
