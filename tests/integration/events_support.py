"""Shared arrangements for the event API tests.

Events are arranged two ways. ``record`` goes through a chat turn and the real
extraction path, as production creates them. ``seed`` writes a row directly, for
instants and stored states extraction cannot produce on demand: a moment just
either side of a date line, or an amount left without its unit.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory.types import (
    MemoryCandidate,
    MemoryDurability,
    MemoryExtraction,
    MemoryType,
)
from app.domain.retrieval.plan import QueryPlan, RetrievalNeed
from app.domain.temporal.ranges import TimeRangeSpec, resolve_range
from app.infrastructure.database.models import Event
from app.retrieval.structured import StructuredActivityQuery
from tests.fakes import FakeLLMProvider
from tests.integration.conftest import sessions_like

PASSWORD = "a-sufficiently-long-password"
EVENTS = "/api/v1/events"
MEMORIES = "/api/v1/memories"
EXACT_HEADING = "Exact figures, computed from the user's records"


class User:
    def __init__(self, user_id: uuid.UUID, headers: dict[str, str]) -> None:
        self.id = user_id
        self.headers = headers


async def register(
    client: AsyncClient, email: str = "ada@example.com", timezone: str | None = None
) -> User:
    registered = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
    )
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    headers = {"Authorization": f"Bearer {response.json()['data']['access_token']}"}
    if timezone is not None:
        updated = await client.patch(
            "/api/v1/profile", headers=headers, json={"timezone": timezone}
        )
        assert updated.status_code == 200, updated.text
    return User(uuid.UUID(registered.json()["data"]["id"]), headers)


def event_candidate(
    description: str,
    activity: str = "study",
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


async def record(
    client: AsyncClient, provider: FakeLLMProvider, user: User, *candidates: MemoryCandidate
) -> None:
    """Record events the way production does: extracted from a chat turn."""
    provider.queue_memory_extractions(MemoryExtraction(candidates=list(candidates)))
    provider.queue_plans(QueryPlan(needs=[RetrievalNeed.SEMANTIC]))
    await client.post("/api/v1/chat", headers=user.headers, json={"message": "Logging."})
    provider.queue_memory_extractions(MemoryExtraction())


async def seed(
    session: AsyncSession,
    user: User,
    occurred_at: datetime,
    activity: str = "study",
    *,
    description: str = "Studied",
    quantity: float | None = None,
    unit: str | None = None,
) -> uuid.UUID:
    """Place an event at an exact instant, for boundaries extraction cannot reach."""
    event = Event(
        id=uuid.uuid4(),
        user_id=user.id,
        activity=activity,
        description=description,
        occurred_at=occurred_at,
        quantity=quantity,
        unit=unit,
    )
    session.add(event)
    await session.commit()
    return event.id


async def listed(client: AsyncClient, user: User, query: str = "") -> dict[str, Any]:
    response = await client.get(f"{EVENTS}{query}", headers=user.headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def only_event(client: AsyncClient, user: User) -> dict[str, Any]:
    [event] = (await listed(client, user))["data"]
    found: dict[str, Any] = event
    return found


async def total_in_prompt(
    client: AsyncClient, provider: FakeLLMProvider, user: User, spec: TimeRangeSpec
) -> str:
    """The exact-figures section the chat would answer from."""
    provider.queue_plans(QueryPlan(needs=[RetrievalNeed.TOTALS], time_range=spec))
    await client.post("/api/v1/chat", headers=user.headers, json={"message": "How much?"})
    prompt = provider.prompts_named("conversation.reply")[-1].user
    if EXACT_HEADING not in prompt:
        return ""
    section = prompt[prompt.index(EXACT_HEADING) :]
    return section[: section.find("The message to answer:")]


def assert_validation_error(response: Any, field: str) -> None:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert field in {detail["field"] for detail in body["error"]["details"]}
    assert body["meta"]["request_id"]


def assert_rule_broken(response: Any, code: str, field: str) -> None:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == code
    assert [detail["field"] for detail in body["error"]["details"]] == [field]
    assert body["meta"]["request_id"]


def local_today(zone: str) -> date:
    return datetime.now(tz=UTC).astimezone(ZoneInfo(zone)).date()


# Every column a PATCH could conceivably write, plus those it must never touch.
COLUMNS = (
    "user_id",
    "activity",
    "description",
    "occurred_at",
    "quantity",
    "unit",
    "memory_id",
    "conversation_id",
    "created_at",
)

# (occurrences, total_quantity, unquantified_occurrences), by (activity, unit).
type Totals = dict[tuple[str, str | None], tuple[int, float | None, int]]


def local_midday(day: date, zone: str) -> datetime:
    return datetime.combine(day, datetime.min.time().replace(hour=12), tzinfo=ZoneInfo(zone))


async def standard_event(
    session: AsyncSession,
    user: User,
    zone: str = "UTC",
    *,
    days_ago: int = 5,
    activity: str = "study",
    quantity: float | None = 2,
    unit: str | None = "hours",
) -> uuid.UUID:
    """An event placed as extraction would place it: local midday, days ago."""
    day = local_today(zone) - timedelta(days=days_ago)
    return await seed(
        session,
        user,
        local_midday(day, zone).astimezone(UTC),
        activity,
        description="Studied algorithms",
        quantity=quantity,
        unit=unit,
    )


async def patch(
    client: AsyncClient, user: User, event_id: uuid.UUID | str, payload: Any
) -> Response:
    return await client.patch(f"{EVENTS}/{event_id}", headers=user.headers, json=payload)


async def event_row(session: AsyncSession, event_id: uuid.UUID | str) -> dict[str, Any] | None:
    """The row as the database holds it now, read past the session's cache; None once gone."""
    session.expire_all()
    event = await session.get(Event, uuid.UUID(str(event_id)))
    if event is None:
        return None
    return {column: getattr(event, column) for column in COLUMNS}


async def totals(
    session: AsyncSession,
    user: User,
    spec: TimeRangeSpec = TimeRangeSpec.ALL_TIME,
    zone: str = "UTC",
    activities: tuple[str, ...] = (),
) -> Totals:
    """What the chat's exact figures would be, straight from the aggregation."""
    summary = await StructuredActivityQuery(sessions_like(session)).summarise(
        user.id,
        resolve_range(spec, datetime.now(tz=UTC), ZoneInfo(zone)),
        activities=activities,
    )
    return {
        (item.activity, item.unit): (
            item.occurrences,
            item.total_quantity,
            item.unquantified_occurrences,
        )
        for item in summary.totals
    }


def changed_columns(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    return {column for column in COLUMNS if before[column] != after[column]}
