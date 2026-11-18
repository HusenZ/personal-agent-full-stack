import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.events.rules import MAX_ACTIVITY_FILTERS
from app.domain.temporal.ranges import TimeRangeSpec
from tests.fakes import FakeLLMProvider
from tests.integration.events_support import (
    EVENTS,
    MEMORIES,
    User,
    assert_rule_broken,
    assert_validation_error,
    event_candidate,
    listed,
    local_today,
    only_event,
    record,
    register,
    seed,
    total_in_prompt,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(replies=["Understood."])


class TestAuthorization:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", EVENTS),
            ("patch", f"{EVENTS}/{uuid.uuid4()}"),
            ("delete", f"{EVENTS}/{uuid.uuid4()}"),
        ],
    )
    async def test_every_endpoint_requires_a_token(
        self, api_client: AsyncClient, method: str, path: str
    ):
        response = await api_client.request(method, path, json={"activity": "x"})

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"


class TestUserIsolation:
    async def test_another_users_events_are_never_listed(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        ada = await register(api_client, "ada@example.com")
        grace = await register(api_client, "grace@example.com")
        await seed(db_session, ada, datetime.now(tz=UTC))

        body = await listed(api_client, grace)

        assert body["data"] == []
        assert body["meta"]["total_items"] == 0


class TestListing:
    async def test_an_empty_list_is_well_formed(self, api_client: AsyncClient):
        user = await register(api_client)

        body = await listed(api_client, user)

        assert body["data"] == []
        assert body["meta"]["total_items"] == 0
        assert body["meta"]["total_pages"] == 0
        assert body["meta"]["has_next"] is False
        assert body["meta"]["request_id"]

    async def test_an_extracted_event_is_listed_with_its_origin(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        user = await register(api_client, timezone="Asia/Kolkata")
        await record(
            api_client,
            llm_provider,
            user,
            event_candidate("Studied algorithms", quantity=2, unit="hours", days_ago=1),
        )

        event = await only_event(api_client, user)
        [memory] = (await api_client.get(MEMORIES, headers=user.headers)).json()["data"]

        assert (event["activity"], event["quantity"], event["unit"]) == ("study", 2, "hours")
        assert event["description"] == "Studied algorithms"
        assert event["timezone"] == "Asia/Kolkata"
        assert event["date"] == (local_today("Asia/Kolkata") - timedelta(days=1)).isoformat()
        assert event["memory_id"] == memory["id"]
        assert event["conversation_id"] is not None

    async def test_newest_first(self, api_client: AsyncClient, db_session: AsyncSession):
        user = await register(api_client)
        now = datetime.now(tz=UTC)
        for days_ago in (3, 0, 7, 1):
            await seed(db_session, user, now - timedelta(days=days_ago), description=f"{days_ago}")

        body = await listed(api_client, user)

        assert [item["description"] for item in body["data"]] == ["0", "1", "3", "7"]

    async def test_events_on_the_same_instant_page_without_overlap_or_gaps(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """Every event on a day shares local midday, so order can't rest on time alone."""
        user = await register(api_client)
        moment = datetime.now(tz=UTC).replace(microsecond=0)
        seeded = {await seed(db_session, user, moment, description=f"{n}") for n in range(5)}

        pages = [await listed(api_client, user, f"?page_size=2&page={n}") for n in (1, 2, 3)]

        ids = [item["id"] for page in pages for item in page["data"]]
        assert len(ids) == 5
        assert {uuid.UUID(item) for item in ids} == seeded
        assert [page["meta"]["has_next"] for page in pages] == [True, True, False]
        assert pages[0]["meta"]["total_pages"] == 3

    async def test_a_page_past_the_end_is_empty_not_an_error(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        await seed(db_session, user, datetime.now(tz=UTC))

        body = await listed(api_client, user, "?page=50")

        assert body["data"] == []
        assert body["meta"]["total_items"] == 1


class TestActivityFilter:
    async def _three_activities(self, session: AsyncSession, user: User) -> None:
        now = datetime.now(tz=UTC)
        for activity in ("study", "running", "reading"):
            await seed(session, user, now, activity, description=activity)

    async def test_only_the_named_activity_is_listed_and_counted(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        await self._three_activities(db_session, user)

        body = await listed(api_client, user, "?activity=running")

        assert [item["activity"] for item in body["data"]] == ["running"]
        assert body["meta"]["total_items"] == 1

    async def test_repeating_the_filter_matches_any_of_them(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        await self._three_activities(db_session, user)

        body = await listed(api_client, user, "?activity=study&activity=reading&activity=study")

        assert sorted(item["activity"] for item in body["data"]) == ["reading", "study"]
        assert body["meta"]["total_items"] == 2

    async def test_the_filter_is_normalised_as_activities_are_stored(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        await self._three_activities(db_session, user)

        body = await listed(api_client, user, "?activity=%20Study%20")

        assert [item["activity"] for item in body["data"]] == ["study"]

    async def test_matching_is_exact_not_by_stem(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """ "run" is not "running": the filter matches the labels the totals group by."""
        user = await register(api_client)
        await self._three_activities(db_session, user)

        body = await listed(api_client, user, "?activity=run")

        assert body["data"] == []


class TestDateFilter:
    async def test_both_ends_of_the_range_are_included(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        for day in (9, 10, 11, 12, 13):
            await seed(
                db_session, user, datetime(2026, 3, day, 12, tzinfo=UTC), description=f"{day}"
            )

        body = await listed(api_client, user, "?start_date=2026-03-10&end_date=2026-03-12")

        assert [item["description"] for item in body["data"]] == ["12", "11", "10"]
        assert body["meta"]["total_items"] == 3

    async def test_a_single_day_range(self, api_client: AsyncClient, db_session: AsyncSession):
        user = await register(api_client)
        await seed(db_session, user, datetime(2026, 3, 10, 0, 0, tzinfo=UTC), description="start")
        await seed(db_session, user, datetime(2026, 3, 10, 23, 59, tzinfo=UTC), description="end")
        await seed(db_session, user, datetime(2026, 3, 11, 0, 0, tzinfo=UTC), description="next")

        body = await listed(api_client, user, "?start_date=2026-03-10&end_date=2026-03-10")

        assert sorted(item["description"] for item in body["data"]) == ["end", "start"]

    async def test_either_end_may_be_left_open(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        for day in (1, 15, 28):
            await seed(
                db_session, user, datetime(2026, 2, day, 12, tzinfo=UTC), description=f"{day}"
            )

        since = await listed(api_client, user, "?start_date=2026-02-15")
        until = await listed(api_client, user, "?end_date=2026-02-15")

        assert [item["description"] for item in since["data"]] == ["28", "15"]
        assert [item["description"] for item in until["data"]] == ["15", "1"]

    @pytest.mark.parametrize(
        ("zone", "instant", "local_day"),
        [
            # Already the 17th in Kiribati while it is the 16th in UTC.
            ("Pacific/Kiritimati", datetime(2026, 11, 16, 22, 0, tzinfo=UTC), "2026-11-17"),
            # Still the 16th in California while it is the 17th in UTC.
            ("America/Los_Angeles", datetime(2026, 11, 17, 5, 0, tzinfo=UTC), "2026-11-16"),
        ],
    )
    async def test_days_are_the_users_days_not_utcs(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        zone: str,
        instant: datetime,
        local_day: str,
    ):
        user = await register(api_client, timezone=zone)
        await seed(db_session, user, instant)
        utc_day = instant.date().isoformat()

        on_local_day = await listed(
            api_client, user, f"?start_date={local_day}&end_date={local_day}"
        )
        on_utc_day = await listed(api_client, user, f"?start_date={utc_day}&end_date={utc_day}")

        [event] = on_local_day["data"]
        assert event["date"] == local_day
        assert event["timezone"] == zone
        assert on_utc_day["data"] == []

    async def test_an_unrecognised_profile_zone_falls_back_to_utc_and_says_so(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """The same fallback the chat's totals use, reported so a client isn't misled."""
        user = await register(api_client, timezone="Mars/Olympus_Mons")
        await seed(db_session, user, datetime(2026, 11, 16, 22, 0, tzinfo=UTC))

        body = await listed(api_client, user, "?start_date=2026-11-16&end_date=2026-11-16")

        [event] = body["data"]
        assert (event["date"], event["timezone"]) == ("2026-11-16", "UTC")

    async def test_a_listed_date_agrees_with_the_period_totals_count_it_in(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
    ):
        """The list and the exact totals must never disagree about which day an event is on."""
        zone = "Pacific/Kiritimati"
        user = await register(api_client, timezone=zone)
        today = local_today(zone)
        noon_local = datetime(today.year, today.month, today.day, 12, tzinfo=ZoneInfo(zone))
        await seed(db_session, user, noon_local.astimezone(UTC), quantity=2, unit="hours")

        body = await listed(api_client, user, f"?start_date={today}&end_date={today}")
        figures = await total_in_prompt(api_client, llm_provider, user, TimeRangeSpec.TODAY)

        assert body["data"][0]["date"] == today.isoformat()
        assert "2 hours" in figures

    async def test_a_range_that_ends_before_it_starts_is_rejected(self, api_client: AsyncClient):
        """An empty list would read as "you did nothing", which is not the answer."""
        user = await register(api_client)

        response = await api_client.get(
            f"{EVENTS}?start_date=2026-03-12&end_date=2026-03-10", headers=user.headers
        )

        assert_rule_broken(response, "INVALID_DATE_RANGE", "query.end_date")


class TestListValidation:
    @pytest.mark.parametrize(
        ("query", "field"),
        [
            ("page=0", "query.page"),
            ("page_size=101", "query.page_size"),
            ("activity=", "query.activity.0"),
            (f"activity={'a' * 101}", "query.activity.0"),
            ("activity=half%00way", "query.activity.0"),
            ("&".join(["activity=a"] * (MAX_ACTIVITY_FILTERS + 1)), "query.activity"),
            ("start_date=2026-13-01", "query.start_date"),
            ("start_date=2026-02-30", "query.start_date"),
            ("start_date=yesterday", "query.start_date"),
            # Instants are refused rather than silently truncated to a day.
            ("start_date=2026-11-01T00:00:00", "query.start_date"),
            ("end_date=2026-11-01T00:00:00Z", "query.end_date"),
            ("start_date=1700000000", "query.start_date"),
            ("start_date=1899-12-31", "query.start_date"),
            ("end_date=3000-01-01", "query.end_date"),
            ("end_date=9999-12-31", "query.end_date"),
        ],
    )
    async def test_invalid_parameters_are_rejected_by_name(
        self, api_client: AsyncClient, query: str, field: str
    ):
        user = await register(api_client)

        response = await api_client.get(f"{EVENTS}?{query}", headers=user.headers)

        assert_validation_error(response, field)

    async def test_the_boundary_dates_are_accepted(self, api_client: AsyncClient):
        user = await register(api_client, timezone="Pacific/Kiritimati")

        body = await listed(api_client, user, "?start_date=1900-01-01&end_date=2999-12-31")

        assert body["data"] == []
