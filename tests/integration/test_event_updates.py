"""PATCH /api/v1/events/{id}: correcting the records exact totals are summed from.

Asserted at three levels: the response, the stored row (so "left unchanged"
means unchanged in the database, not merely in what was echoed back), and the
totals computed from it (so a correction changes the answers it should, and
only those).
"""

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.events.rules import MAX_EVENT_QUANTITY
from app.domain.temporal.ranges import TimeRangeSpec, resolve_range
from app.retrieval.temporal import TemporalEventRetriever
from tests.fakes import FakeLLMProvider
from tests.integration.conftest import sessions_like
from tests.integration.events_support import (
    EVENTS,
    MEMORIES,
    assert_rule_broken,
    assert_validation_error,
    changed_columns,
    event_candidate,
    event_row,
    listed,
    local_midday,
    local_today,
    only_event,
    patch,
    record,
    register,
    seed,
    standard_event,
    total_in_prompt,
    totals,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(replies=["Understood."])


async def stored(session: AsyncSession, event_id: uuid.UUID | str) -> dict[str, Any]:
    """The row as the database holds it now; it must still exist."""
    row = await event_row(session, event_id)
    assert row is not None
    return row


class TestIndividualFields:
    @pytest.mark.parametrize(
        ("payload", "column", "expected"),
        [
            ({"activity": "reading"}, "activity", "reading"),
            ({"description": "Read a paper"}, "description", "Read a paper"),
            ({"quantity": 3.5}, "quantity", 3.5),
            ({"unit": "minutes"}, "unit", "minutes"),
        ],
    )
    async def test_one_field_changes_and_no_other_column_is_written(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        payload: dict[str, Any],
        column: str,
        expected: Any,
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)
        before_row = await stored(db_session, event_id)
        before = await only_event(api_client, user)

        response = await patch(api_client, user, event_id, payload)

        assert response.status_code == 200, response.text
        after_row = await stored(db_session, event_id)
        assert changed_columns(before_row, after_row) == {column}
        assert after_row[column] == expected
        after = response.json()["data"]
        unchanged = set(before) - {column, "updated_at"}
        assert {key: after[key] for key in unchanged} == {key: before[key] for key in unchanged}
        assert await only_event(api_client, user) == after

    async def test_the_date_alone_moves_only_the_instant(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)
        before_row = await stored(db_session, event_id)
        new_day = local_today("UTC") - timedelta(days=2)

        response = await patch(api_client, user, event_id, {"date": new_day.isoformat()})

        assert response.status_code == 200, response.text
        after_row = await stored(db_session, event_id)
        assert changed_columns(before_row, after_row) == {"occurred_at"}
        assert response.json()["data"]["date"] == new_day.isoformat()

    @pytest.mark.parametrize(
        ("payload", "field", "expected"),
        [
            ({"activity": "  Reading \t"}, "activity", "reading"),
            ({"activity": "STUDY"}, "activity", "study"),
            ({"unit": " Minutes"}, "unit", "minutes"),
            ({"description": "  Read a paper \n"}, "description", "Read a paper"),
        ],
    )
    async def test_values_are_normalised_as_extraction_normalises_them(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        payload: dict[str, Any],
        field: str,
        expected: str,
    ):
        """So an edited event aggregates with the extracted ones it belongs with."""
        user = await register(api_client)
        event_id = await standard_event(db_session, user)

        response = await patch(api_client, user, event_id, payload)

        assert response.json()["data"][field] == expected
        assert (await stored(db_session, event_id))[field] == expected

    async def test_a_whole_number_amount_is_stored_as_a_number(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)

        response = await patch(api_client, user, event_id, {"quantity": 4})

        assert response.json()["data"]["quantity"] == 4
        assert (await stored(db_session, event_id))["quantity"] == 4.0

    async def test_repeating_the_same_update_gives_the_same_result(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)
        payload = {"quantity": 3, "description": "Studied graphs"}

        first = (await patch(api_client, user, event_id, payload)).json()["data"]
        second = (await patch(api_client, user, event_id, payload)).json()["data"]

        first.pop("updated_at")
        second.pop("updated_at")
        assert first == second

    async def test_updated_at_moves_and_created_at_does_not(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)
        before = await only_event(api_client, user)

        after = (await patch(api_client, user, event_id, {"quantity": 3})).json()["data"]

        assert after["created_at"] == before["created_at"]
        assert datetime.fromisoformat(after["updated_at"]) > datetime.fromisoformat(
            before["updated_at"]
        )

    async def test_editing_an_extracted_event_leaves_its_memory_alone(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """A memory can carry several occurrences; correcting one says nothing of the belief."""
        user = await register(api_client)
        await record(
            api_client,
            llm_provider,
            user,
            event_candidate("Studied algorithms", quantity=2, unit="hours", days_ago=1),
        )
        event = await only_event(api_client, user)
        [memory_before] = (await api_client.get(MEMORIES, headers=user.headers)).json()["data"]

        response = await patch(
            api_client,
            user,
            event["id"],
            {"description": "Corrected", "activity": "reading", "quantity": 9},
        )

        assert response.json()["data"]["memory_id"] == memory_before["id"]
        [memory_after] = (await api_client.get(MEMORIES, headers=user.headers)).json()["data"]
        assert memory_after == memory_before


class TestCombinedUpdates:
    async def test_every_field_at_once(self, api_client: AsyncClient, db_session: AsyncSession):
        zone = "Asia/Kolkata"
        user = await register(api_client, timezone=zone)
        event_id = await standard_event(db_session, user, zone)
        new_day = local_today(zone) - timedelta(days=1)

        response = await patch(
            api_client,
            user,
            event_id,
            {
                "activity": "Running",
                "description": "Ran along the river",
                "date": new_day.isoformat(),
                "quantity": 5,
                "unit": "KM",
            },
        )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert (data["activity"], data["description"], data["date"]) == (
            "running",
            "Ran along the river",
            new_day.isoformat(),
        )
        assert (data["quantity"], data["unit"]) == (5, "km")
        row = await stored(db_session, event_id)
        assert row["occurred_at"] == local_midday(new_day, zone)
        assert row["memory_id"] is None

    async def test_a_replacement_and_a_clearing_together(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)
        before_row = await stored(db_session, event_id)

        response = await patch(
            api_client,
            user,
            event_id,
            {"activity": "reading", "quantity": None, "unit": None},
        )

        assert response.status_code == 200, response.text
        after_row = await stored(db_session, event_id)
        assert changed_columns(before_row, after_row) == {"activity", "quantity", "unit"}
        assert (after_row["quantity"], after_row["unit"]) == (None, None)

    async def test_one_invalid_field_rejects_the_whole_update(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """All or nothing: half an edit applied is a record nobody asked for."""
        user = await register(api_client)
        event_id = await standard_event(db_session, user)
        before_row = await stored(db_session, event_id)

        response = await patch(api_client, user, event_id, {"activity": "reading", "quantity": -1})

        assert_validation_error(response, "body.quantity")
        assert await stored(db_session, event_id) == before_row

    async def test_a_broken_rule_rejects_the_whole_update(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user, quantity=None, unit=None)
        before_row = await stored(db_session, event_id)

        response = await patch(
            api_client, user, event_id, {"description": "Studied graphs", "quantity": 5}
        )

        assert_rule_broken(response, "EVENT_AMOUNT_INCOMPLETE", "body.unit")
        assert await stored(db_session, event_id) == before_row

    async def test_every_invalid_field_is_reported_not_just_the_first(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)

        response = await patch(
            api_client,
            user,
            event_id,
            {"activity": "", "description": "", "date": "soon", "quantity": -1, "unit": ""},
        )

        assert response.status_code == 422
        fields = {detail["field"] for detail in response.json()["error"]["details"]}
        assert fields == {
            "body.activity",
            "body.description",
            "body.date",
            "body.quantity",
            "body.unit",
        }


class TestExplicitNulls:
    async def test_null_clears_an_amount_but_absent_leaves_it(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)

        absent = await patch(api_client, user, event_id, {"description": "Studied graphs"})
        cleared = await patch(api_client, user, event_id, {"quantity": None, "unit": None})

        assert (absent.json()["data"]["quantity"], absent.json()["data"]["unit"]) == (2, "hours")
        assert (cleared.json()["data"]["quantity"], cleared.json()["data"]["unit"]) == (None, None)
        row = await stored(db_session, event_id)
        assert (row["quantity"], row["unit"], row["description"]) == (None, None, "Studied graphs")

    async def test_clearing_an_amount_that_is_already_empty_is_harmless(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user, quantity=None, unit=None)

        response = await patch(api_client, user, event_id, {"quantity": None, "unit": None})

        assert response.status_code == 200, response.text

    @pytest.mark.parametrize("field", ["activity", "description", "date"])
    async def test_a_field_every_event_has_cannot_be_nulled(
        self, api_client: AsyncClient, db_session: AsyncSession, field: str
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)
        before_row = await stored(db_session, event_id)

        response = await patch(api_client, user, event_id, {field: None})

        assert_validation_error(response, f"body.{field}")
        assert await stored(db_session, event_id) == before_row

    async def test_an_all_null_update_of_required_fields_names_each(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)

        response = await patch(
            api_client, user, event_id, {"activity": None, "description": None, "date": None}
        )

        assert response.status_code == 422
        fields = {detail["field"] for detail in response.json()["error"]["details"]}
        assert fields == {"body.activity", "body.description", "body.date"}


class TestAmountPairing:
    @pytest.mark.parametrize(
        ("stored_amount", "payload", "blamed"),
        [
            # Clearing one half of an amount leaves the other meaningless.
            ((2, "hours"), {"quantity": None}, "body.unit"),
            ((2, "hours"), {"unit": None}, "body.quantity"),
            ((2, "hours"), {"quantity": 3, "unit": None}, "body.unit"),
            # Adding one half to an event that had no amount.
            ((None, None), {"quantity": 2}, "body.unit"),
            ((None, None), {"unit": "hours"}, "body.quantity"),
            ((None, None), {"quantity": None, "unit": "hours"}, "body.quantity"),
        ],
    )
    async def test_an_edit_cannot_leave_an_amount_without_its_unit(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        stored_amount: tuple[float | None, str | None],
        payload: dict[str, Any],
        blamed: str,
    ):
        """Totals are grouped by unit; an amount without one is summed but never shown.

        The error names the field to send: the missing half when only one was
        sent, or the empty one when both were.
        """
        user = await register(api_client)
        quantity, unit = stored_amount
        event_id = await standard_event(db_session, user, quantity=quantity, unit=unit)
        before_row = await stored(db_session, event_id)

        response = await patch(api_client, user, event_id, payload)

        assert_rule_broken(response, "EVENT_AMOUNT_INCOMPLETE", blamed)
        assert await stored(db_session, event_id) == before_row

    @pytest.mark.parametrize(
        "payload", [{"quantity": 3}, {"unit": "minutes"}, {"quantity": 0, "unit": "km"}]
    )
    async def test_either_half_of_a_complete_amount_changes_alone(
        self, api_client: AsyncClient, db_session: AsyncSession, payload: dict[str, Any]
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)

        response = await patch(api_client, user, event_id, payload)

        assert response.status_code == 200, response.text

    async def test_a_stored_incomplete_amount_does_not_block_other_edits(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """Extraction can store an amount without a unit; that must not lock the row."""
        user = await register(api_client)
        event_id = await standard_event(db_session, user, quantity=2, unit=None)

        described = await patch(api_client, user, event_id, {"description": "Studied"})
        dated = await patch(api_client, user, event_id, {"date": local_today("UTC").isoformat()})
        repaired = await patch(api_client, user, event_id, {"unit": "hours"})

        assert described.status_code == 200, described.text
        assert dated.status_code == 200, dated.text
        assert (repaired.json()["data"]["quantity"], repaired.json()["data"]["unit"]) == (
            2,
            "hours",
        )

    async def test_concurrent_edits_never_leave_an_incoherent_amount(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """Each edit is checked against the row it lands on, under a lock.

        Unlocked, clearing the amount and changing its quantity could both pass
        against the same stored row and together leave a quantity with no unit.
        """
        user = await register(api_client)
        event_id = await standard_event(db_session, user)

        for _ in range(5):
            await asyncio.gather(
                patch(api_client, user, event_id, {"quantity": None, "unit": None}),
                patch(api_client, user, event_id, {"quantity": 5}),
            )
            row = await stored(db_session, event_id)
            assert (row["quantity"] is None) == (row["unit"] is None)
            await patch(api_client, user, event_id, {"quantity": 2, "unit": "hours"})


class TestDates:
    @pytest.mark.parametrize(
        "zone",
        [
            "UTC",
            "Asia/Kolkata",
            "America/Los_Angeles",
            # Local midday is on the previous UTC day here...
            "Pacific/Kiritimati",
            # ...and on the next UTC day here.
            "Etc/GMT+12",
        ],
    )
    async def test_a_date_is_stored_as_local_midday_in_the_users_zone(
        self, api_client: AsyncClient, db_session: AsyncSession, zone: str
    ):
        user = await register(api_client, timezone=zone)
        event_id = await standard_event(db_session, user, zone)
        new_day = local_today(zone) - timedelta(days=3)

        response = await patch(api_client, user, event_id, {"date": new_day.isoformat()})

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert (data["date"], data["timezone"]) == (new_day.isoformat(), zone)
        assert (await stored(db_session, event_id))["occurred_at"] == local_midday(new_day, zone)
        assert datetime.fromisoformat(data["occurred_at"]) == local_midday(new_day, zone)
        found = await listed(
            api_client, user, f"?start_date={new_day.isoformat()}&end_date={new_day.isoformat()}"
        )
        assert [item["id"] for item in found["data"]] == [str(event_id)]

    @pytest.mark.parametrize(
        ("day", "utc_midday"),
        [
            # Clocks go forward at 02:00: midday is EDT, four hours behind UTC.
            (date(2026, 3, 8), datetime(2026, 3, 8, 16, 0, tzinfo=UTC)),
            # Clocks go back at 02:00: midday is EST, five hours behind UTC.
            (date(2026, 11, 1), datetime(2026, 11, 1, 17, 0, tzinfo=UTC)),
        ],
    )
    async def test_a_day_when_the_clocks_change_still_lands_at_local_midday(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        day: date,
        utc_midday: datetime,
    ):
        user = await register(api_client, timezone="America/New_York")
        event_id = await standard_event(db_session, user, "America/New_York")

        response = await patch(api_client, user, event_id, {"date": day.isoformat()})

        assert response.json()["data"]["date"] == day.isoformat()
        assert (await stored(db_session, event_id))["occurred_at"] == utc_midday

    async def test_an_event_off_the_midday_convention_is_placed_on_it_when_redated(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await seed(db_session, user, datetime.now(tz=UTC).replace(hour=3, minute=7))
        new_day = local_today("UTC") - timedelta(days=1)

        await patch(api_client, user, event_id, {"date": new_day.isoformat()})

        assert (await stored(db_session, event_id))["occurred_at"] == local_midday(new_day, "UTC")

    async def test_the_date_is_read_in_the_profiles_current_zone(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """A user who moved has their corrections placed where they now live."""
        user = await register(api_client, timezone="Asia/Kolkata")
        event_id = await standard_event(db_session, user, "Asia/Kolkata")
        await api_client.patch(
            "/api/v1/profile", headers=user.headers, json={"timezone": "America/Los_Angeles"}
        )
        new_day = local_today("America/Los_Angeles") - timedelta(days=1)

        response = await patch(api_client, user, event_id, {"date": new_day.isoformat()})

        assert response.json()["data"]["timezone"] == "America/Los_Angeles"
        assert (await stored(db_session, event_id))["occurred_at"] == local_midday(
            new_day, "America/Los_Angeles"
        )

    async def test_an_unrecognised_profile_zone_places_the_date_in_utc(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client, timezone="Mars/Olympus_Mons")
        event_id = await standard_event(db_session, user)
        new_day = local_today("UTC") - timedelta(days=1)

        response = await patch(api_client, user, event_id, {"date": new_day.isoformat()})

        assert response.json()["data"]["timezone"] == "UTC"
        assert (await stored(db_session, event_id))["occurred_at"] == local_midday(new_day, "UTC")

    async def test_today_in_the_users_zone_is_accepted_even_when_utc_is_behind(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        zone = "Pacific/Kiritimati"
        user = await register(api_client, timezone=zone)
        event_id = await standard_event(db_session, user, zone)
        today = local_today(zone)

        response = await patch(api_client, user, event_id, {"date": today.isoformat()})

        assert response.status_code == 200, response.text
        assert response.json()["data"]["date"] == today.isoformat()

    @pytest.mark.parametrize("zone", ["America/Los_Angeles", "Pacific/Kiritimati"])
    async def test_a_day_after_the_users_today_is_rejected(
        self, api_client: AsyncClient, db_session: AsyncSession, zone: str
    ):
        """An event is something the user did. Plans are held elsewhere."""
        user = await register(api_client, timezone=zone)
        event_id = await standard_event(db_session, user, zone)
        before_row = await stored(db_session, event_id)
        tomorrow = local_today(zone) + timedelta(days=1)

        response = await patch(api_client, user, event_id, {"date": tomorrow.isoformat()})

        assert_rule_broken(response, "EVENT_DATE_IN_FUTURE", "body.date")
        assert await stored(db_session, event_id) == before_row

    async def test_the_earliest_allowed_day_is_accepted(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client, timezone="Pacific/Kiritimati")
        event_id = await standard_event(db_session, user, "Pacific/Kiritimati")

        response = await patch(api_client, user, event_id, {"date": "1900-01-01"})

        assert response.status_code == 200, response.text
        assert response.json()["data"]["date"] == "1900-01-01"


class TestValidation:
    @pytest.mark.parametrize(
        ("payload", "field"),
        [
            ({}, "body"),
            ({"user_id": str(uuid.uuid4())}, "body"),
            (["not", "an", "object"], "body"),
            ("a string", "body"),
            ({"activity": ""}, "body.activity"),
            ({"activity": "   "}, "body.activity"),
            ({"activity": "a" * 101}, "body.activity"),
            ({"activity": "half\u0000way"}, "body.activity"),
            ({"activity": 7}, "body.activity"),
            ({"activity": ["study"]}, "body.activity"),
            ({"description": ""}, "body.description"),
            ({"description": " \n\t "}, "body.description"),
            ({"description": "a" * 1001}, "body.description"),
            ({"description": "half\u0000way"}, "body.description"),
            ({"description": 42}, "body.description"),
            ({"unit": ""}, "body.unit"),
            ({"unit": "   "}, "body.unit"),
            ({"unit": "a" * 33}, "body.unit"),
            ({"unit": "half\u0000way"}, "body.unit"),
            ({"unit": 5}, "body.unit"),
            ({"quantity": -0.5}, "body.quantity"),
            ({"quantity": -1}, "body.quantity"),
            ({"quantity": MAX_EVENT_QUANTITY + 1}, "body.quantity"),
            # Lax parsing would turn these into amounts nobody entered.
            ({"quantity": "2"}, "body.quantity"),
            ({"quantity": "NaN"}, "body.quantity"),
            ({"quantity": True}, "body.quantity"),
            ({"quantity": [2]}, "body.quantity"),
            ({"quantity": {"value": 2}}, "body.quantity"),
            ({"date": "2026-02-30"}, "body.date"),
            ({"date": "2026-13-01"}, "body.date"),
            ({"date": "05/01/2026"}, "body.date"),
            ({"date": "yesterday"}, "body.date"),
            # Instants are refused rather than silently cut down to a day.
            ({"date": "2026-01-05T12:00:00"}, "body.date"),
            ({"date": "2026-01-05T00:00:00Z"}, "body.date"),
            ({"date": 1767571200}, "body.date"),
            ({"date": "1899-12-31"}, "body.date"),
        ],
    )
    async def test_invalid_values_are_rejected_by_field_and_change_nothing(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        payload: Any,
        field: str,
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)
        before_row = await stored(db_session, event_id)

        response = await patch(api_client, user, event_id, payload)

        assert_validation_error(response, field)
        assert await stored(db_session, event_id) == before_row

    @pytest.mark.parametrize(
        "raw",
        [
            '{"quantity": NaN}',
            '{"quantity": Infinity}',
            '{"quantity": -Infinity}',
            '{"quantity": 1e400}',
            '{"activity": ',
            "not json",
        ],
    )
    async def test_non_standard_or_broken_json_is_rejected(
        self, api_client: AsyncClient, db_session: AsyncSession, raw: str
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)
        before_row = await stored(db_session, event_id)

        response = await api_client.patch(
            f"{EVENTS}/{event_id}",
            headers={**user.headers, "Content-Type": "application/json"},
            content=raw,
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert await stored(db_session, event_id) == before_row

    async def test_a_missing_body_is_rejected(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)

        response = await api_client.patch(f"{EVENTS}/{event_id}", headers=user.headers)

        assert_validation_error(response, "body")

    async def test_the_boundaries_themselves_are_accepted(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)

        for payload in (
            {"quantity": 0},
            {"quantity": MAX_EVENT_QUANTITY},
            {"description": "a" * 1000},
            {"description": "x"},
            {"activity": "a" * 100},
            {"activity": "a"},
            {"unit": "a" * 32},
            {"unit": "h"},
        ):
            response = await patch(api_client, user, event_id, payload)
            assert response.status_code == 200, (payload, response.text)

    async def test_fields_a_client_may_not_set_are_ignored(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """Extra fields are not an error, but they must never be applied."""
        ada = await register(api_client, "ada@example.com")
        grace = await register(api_client, "grace@example.com")
        event_id = await standard_event(db_session, ada)
        before_row = await stored(db_session, event_id)

        response = await patch(
            api_client,
            ada,
            event_id,
            {
                "quantity": 3,
                "id": str(uuid.uuid4()),
                "user_id": str(grace.id),
                "memory_id": str(uuid.uuid4()),
                "conversation_id": str(uuid.uuid4()),
                "occurred_at": "2020-01-01T00:00:00Z",
                "created_at": "2020-01-01T00:00:00Z",
                "timezone": "Asia/Tokyo",
            },
        )

        assert response.status_code == 200, response.text
        after_row = await stored(db_session, event_id)
        assert changed_columns(before_row, after_row) == {"quantity"}
        assert response.json()["data"]["timezone"] == "UTC"
        assert (await listed(api_client, grace))["data"] == []

    async def test_a_malformed_id_is_a_validation_error(self, api_client: AsyncClient):
        user = await register(api_client)

        response = await patch(api_client, user, "not-a-uuid", {"quantity": 1})

        assert_validation_error(response, "path.event_id")


class TestMissingAndForeignEvents:
    async def test_an_event_that_does_not_exist_reports_not_found(self, api_client: AsyncClient):
        user = await register(api_client)

        response = await patch(api_client, user, uuid.uuid4(), {"quantity": 1})

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "EVENT_NOT_FOUND"
        assert response.json()["meta"]["request_id"]

    async def test_a_deleted_event_reports_not_found(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)
        await api_client.delete(f"{EVENTS}/{event_id}", headers=user.headers)

        response = await patch(api_client, user, event_id, {"description": "Back again"})

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "EVENT_NOT_FOUND"

    async def test_another_users_event_reports_exactly_what_a_missing_one_does(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """Nothing in the answer may tell a caller that the id exists."""
        ada = await register(api_client, "ada@example.com")
        grace = await register(api_client, "grace@example.com")
        event_id = await standard_event(db_session, ada)

        foreign = await patch(api_client, grace, event_id, {"quantity": 99})
        missing = await patch(api_client, grace, uuid.uuid4(), {"quantity": 99})

        assert foreign.status_code == missing.status_code == 404
        assert foreign.json()["error"] == missing.json()["error"]

    @pytest.mark.parametrize(
        "payload",
        [
            {"quantity": 99},
            {"activity": "hacked", "description": "hacked", "unit": "km"},
            {"quantity": None, "unit": None},
            {"date": "2026-01-01"},
        ],
    )
    async def test_another_users_event_is_never_changed(
        self, api_client: AsyncClient, db_session: AsyncSession, payload: dict[str, Any]
    ):
        ada = await register(api_client, "ada@example.com")
        grace = await register(api_client, "grace@example.com")
        event_id = await standard_event(db_session, ada)
        before_row = await stored(db_session, event_id)

        response = await patch(api_client, grace, event_id, payload)

        assert response.status_code == 404
        assert await stored(db_session, event_id) == before_row
        assert (await only_event(api_client, ada))["quantity"] == 2

    async def test_a_rule_an_edit_would_break_does_not_reveal_a_foreign_event(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """Ownership is settled before any rule that reads the stored row.

        Otherwise an update that would leave half an amount, or move the event
        into the future, would answer 422 for someone else's event and 404 for
        an id that does not exist, and the difference would confirm the id.
        """
        ada = await register(api_client, "ada@example.com")
        grace = await register(api_client, "grace@example.com")
        event_id = await standard_event(db_session, ada, quantity=None, unit=None)
        tomorrow = (local_today("UTC") + timedelta(days=2)).isoformat()

        incomplete = await patch(api_client, grace, event_id, {"quantity": 5})
        future = await patch(api_client, grace, event_id, {"date": tomorrow})

        for response in (incomplete, future):
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "EVENT_NOT_FOUND"

    async def test_the_owner_can_still_edit_after_a_foreign_attempt(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        ada = await register(api_client, "ada@example.com")
        grace = await register(api_client, "grace@example.com")
        event_id = await standard_event(db_session, ada)
        await patch(api_client, grace, event_id, {"quantity": 99})

        response = await patch(api_client, ada, event_id, {"quantity": 3})

        assert response.status_code == 200
        assert response.json()["data"]["quantity"] == 3

    async def test_a_token_is_required(self, api_client: AsyncClient):
        response = await api_client.patch(f"{EVENTS}/{uuid.uuid4()}", json={"quantity": 1})

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"


class TestAggregation:
    async def test_a_corrected_amount_changes_the_sum(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        await standard_event(db_session, user, quantity=2)
        corrected = await standard_event(db_session, user, quantity=3, days_ago=1)

        await patch(api_client, user, corrected, {"quantity": 5})

        assert await totals(db_session, user) == {("study", "hours"): (2, 7.0, 0)}

    async def test_a_corrected_activity_moves_the_event_between_totals(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        await standard_event(db_session, user, quantity=2)
        moved = await standard_event(db_session, user, quantity=3, days_ago=1)

        await patch(api_client, user, moved, {"activity": "Reading"})

        assert await totals(db_session, user) == {
            ("study", "hours"): (1, 2.0, 0),
            ("reading", "hours"): (1, 3.0, 0),
        }
        assert await totals(db_session, user, activities=("reading",)) == {
            ("reading", "hours"): (1, 3.0, 0)
        }

    async def test_a_normalised_activity_joins_the_total_it_belongs_to(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        await standard_event(db_session, user, activity="study", quantity=2)
        stray = await standard_event(db_session, user, activity="studying", quantity=1)
        assert len(await totals(db_session, user)) == 2

        await patch(api_client, user, stray, {"activity": "  Study "})

        assert await totals(db_session, user) == {("study", "hours"): (2, 3.0, 0)}

    async def test_a_corrected_unit_regroups_the_amount(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """Amounts are only ever added within one unit, so fixing "hrs" merges the groups."""
        user = await register(api_client)
        await standard_event(db_session, user, quantity=2, unit="hours")
        stray = await standard_event(db_session, user, quantity=1, unit="hrs")
        assert await totals(db_session, user) == {
            ("study", "hours"): (1, 2.0, 0),
            ("study", "hrs"): (1, 1.0, 0),
        }

        await patch(api_client, user, stray, {"unit": "Hours"})

        assert await totals(db_session, user) == {("study", "hours"): (2, 3.0, 0)}

    async def test_a_changed_unit_stops_being_added_to_the_old_one(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        await standard_event(db_session, user, quantity=2, unit="hours")
        converted = await standard_event(db_session, user, quantity=1, unit="hours")

        await patch(api_client, user, converted, {"quantity": 45, "unit": "minutes"})

        assert await totals(db_session, user) == {
            ("study", "hours"): (1, 2.0, 0),
            ("study", "minutes"): (1, 45.0, 0),
        }

    async def test_a_cleared_amount_is_counted_as_an_occurrence_without_one(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        await standard_event(db_session, user, quantity=2)
        cleared = await standard_event(db_session, user, quantity=3, days_ago=1)

        await patch(api_client, user, cleared, {"quantity": None, "unit": None})

        assert await totals(db_session, user) == {
            ("study", "hours"): (1, 2.0, 0),
            ("study", None): (1, None, 1),
        }

    async def test_an_amount_added_later_is_summed(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        untimed = await standard_event(db_session, user, quantity=None, unit=None)

        await patch(api_client, user, untimed, {"quantity": 1.5, "unit": "hours"})

        assert await totals(db_session, user) == {("study", "hours"): (1, 1.5, 0)}

    async def test_a_redated_event_moves_between_the_users_days(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """Judged in the user's calendar, far from UTC, where a day's edge matters most."""
        zone = "Pacific/Kiritimati"
        user = await register(api_client, timezone=zone)
        event_id = await standard_event(db_session, user, zone, days_ago=0)
        assert await totals(db_session, user, TimeRangeSpec.TODAY, zone) == {
            ("study", "hours"): (1, 2.0, 0)
        }

        yesterday = local_today(zone) - timedelta(days=1)
        await patch(api_client, user, event_id, {"date": yesterday.isoformat()})

        assert await totals(db_session, user, TimeRangeSpec.TODAY, zone) == {}
        assert await totals(db_session, user, TimeRangeSpec.YESTERDAY, zone) == {
            ("study", "hours"): (1, 2.0, 0)
        }
        assert await totals(db_session, user, TimeRangeSpec.LAST_7_DAYS, zone) == {
            ("study", "hours"): (1, 2.0, 0)
        }

    async def test_a_redated_event_leaves_a_period_it_no_longer_falls_in(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user, days_ago=1)

        older = local_today("UTC") - timedelta(days=40)
        await patch(api_client, user, event_id, {"date": older.isoformat()})

        assert await totals(db_session, user, TimeRangeSpec.LAST_30_DAYS) == {}
        assert await totals(db_session, user, TimeRangeSpec.ALL_TIME) == {
            ("study", "hours"): (1, 2.0, 0)
        }

    async def test_a_rejected_update_leaves_the_totals_alone(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)
        before = await totals(db_session, user)

        await patch(api_client, user, event_id, {"quantity": None})
        await patch(api_client, user, event_id, {"activity": "reading", "quantity": -1})

        assert await totals(db_session, user) == before

    async def test_another_users_attempt_leaves_the_owners_totals_alone(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        ada = await register(api_client, "ada@example.com")
        grace = await register(api_client, "grace@example.com")
        event_id = await standard_event(db_session, ada)

        await patch(api_client, grace, event_id, {"quantity": 99, "activity": "reading"})

        assert await totals(db_session, ada) == {("study", "hours"): (1, 2.0, 0)}
        assert await totals(db_session, grace) == {}

    async def test_a_corrected_description_reaches_the_timeline(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user, days_ago=1)

        await patch(api_client, user, event_id, {"description": "Studied graph theory"})

        entries = await TemporalEventRetriever(sessions_like(db_session)).timeline(
            user.id, resolve_range(TimeRangeSpec.LAST_7_DAYS, datetime.now(tz=UTC))
        )
        assert [entry.description for entry in entries] == ["Studied graph theory"]

    async def test_the_corrected_figure_is_the_one_the_chat_answers_with(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
        db_session: AsyncSession,
    ):
        """End to end: an extracted event, corrected, then asked about."""
        user = await register(api_client)
        await record(
            api_client,
            llm_provider,
            user,
            event_candidate("Studied algorithms", quantity=2, unit="hours", days_ago=1),
        )
        event = await only_event(api_client, user)

        await patch(api_client, user, event["id"], {"quantity": 4})
        figures = await total_in_prompt(api_client, llm_provider, user, TimeRangeSpec.LAST_7_DAYS)

        assert "4 hours" in figures
        assert "2 hours" not in figures
