"""DELETE /api/v1/events/{id}: removing exactly one occurrence.

A restated event reinforces one memory but records an event each time, so a
memory can own several occurrences. Deleting the memory takes all of them;
this endpoint exists to take just one. Everything here is asserted against the
database as well as the API: the deleted row, its siblings, its memory and the
totals computed from what is left.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.events.dto import UpdateEventCommand
from app.application.events.service import EventService
from app.domain.temporal.ranges import TimeRangeSpec, resolve_range
from app.infrastructure.database.models import Event, Memory, MemoryVersion, User
from app.infrastructure.database.repositories.event import EventRepository
from app.infrastructure.database.repositories.profile import ProfileRepository
from app.retrieval.temporal import TemporalEventRetriever
from tests.fakes import FakeLLMProvider
from tests.integration.conftest import sessions_like
from tests.integration.events_support import (
    EVENTS,
    MEMORIES,
    assert_validation_error,
    event_candidate,
    event_row,
    listed,
    patch,
    record,
    register,
    seed,
    standard_event,
    total_in_prompt,
    totals,
)
from tests.integration.events_support import User as ApiUser

pytestmark = pytest.mark.integration


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(replies=["Understood."])


async def delete(client: AsyncClient, user: ApiUser, event_id: uuid.UUID | str) -> Response:
    return await client.delete(f"{EVENTS}/{event_id}", headers=user.headers)


def event_service(session: AsyncSession) -> EventService:
    return EventService(session, EventRepository(session), ProfileRepository(session))


async def event_count(session: AsyncSession) -> int:
    session.expire_all()
    return int(await session.scalar(select(func.count()).select_from(Event)) or 0)


async def memory_state(session: AsyncSession, memory_id: uuid.UUID | str) -> dict[str, Any]:
    """A memory's stored columns and the length of its history.

    Compared before and after a deletion: "preserved" means the row is still
    there and nothing about it, including updated_at, was rewritten.
    """
    session.expire_all()
    memory = await session.get(Memory, uuid.UUID(str(memory_id)))
    assert memory is not None, "the memory was deleted"
    versions = await session.scalar(
        select(func.count()).select_from(MemoryVersion).where(MemoryVersion.memory_id == memory.id)
    )
    return {
        "content": memory.content,
        "status": memory.status,
        "importance": memory.importance,
        "confidence": memory.confidence,
        "expires_at": memory.expires_at,
        "updated_at": memory.updated_at,
        "versions": versions,
    }


async def occurrences(
    client: AsyncClient, provider: FakeLLMProvider, user: ApiUser, *quantities: float
) -> list[dict[str, Any]]:
    """The same event stated once per quantity, as separate chat turns.

    Each restatement reinforces the one memory and records another event, which
    is the case single-event deletion exists for. Returned oldest first.
    """
    # The first quantity is stated as the longest ago, so the list reads in order.
    for index, quantity in enumerate(quantities):
        days_ago = len(quantities) - index
        await record(
            client,
            provider,
            user,
            event_candidate(
                "Studied algorithms", quantity=quantity, unit="hours", days_ago=days_ago
            ),
        )
    events = list(reversed((await listed(client, user))["data"]))
    assert len(events) == len(quantities)
    assert len({event["memory_id"] for event in events}) == 1, "expected one shared memory"
    return events


class TestDeletion:
    async def test_the_event_is_gone_and_nothing_is_returned(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """204 with no body, as deleting a memory or a conversation answers."""
        user = await register(api_client)
        event_id = await standard_event(db_session, user)

        response = await delete(api_client, user, event_id)

        assert response.status_code == 204
        assert response.content == b""
        assert await event_row(db_session, event_id) is None
        assert (await listed(api_client, user))["data"] == []

    async def test_exactly_one_row_is_removed(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        ada = await register(api_client, "ada@example.com")
        grace = await register(api_client, "grace@example.com")
        doomed = await standard_event(db_session, ada, days_ago=2)
        kept = [
            await standard_event(db_session, ada, days_ago=1),
            await standard_event(db_session, ada, days_ago=3),
            await standard_event(db_session, grace, days_ago=2),
        ]
        kept_rows = [await event_row(db_session, event_id) for event_id in kept]
        before = await event_count(db_session)

        await delete(api_client, ada, doomed)

        assert await event_count(db_session) == before - 1
        assert [await event_row(db_session, event_id) for event_id in kept] == kept_rows

    async def test_an_event_with_no_memory_can_be_deleted(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """Events seeded for evaluation, or left behind by a nulled link, have none."""
        user = await register(api_client)
        event_id = await seed(db_session, user, datetime.now(tz=UTC))
        assert (await event_row(db_session, event_id) or {})["memory_id"] is None

        response = await delete(api_client, user, event_id)

        assert response.status_code == 204

    async def test_an_event_whose_conversation_was_deleted_can_still_be_deleted(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        user = await register(api_client)
        [event] = await occurrences(api_client, llm_provider, user, 2)
        await api_client.delete(
            f"/api/v1/conversations/{event['conversation_id']}", headers=user.headers
        )
        assert (await event_row(db_session, event["id"]) or {})["conversation_id"] is None

        response = await delete(api_client, user, event["id"])

        assert response.status_code == 204
        await memory_state(db_session, event["memory_id"])

    async def test_a_deleted_event_is_left_out_of_the_account_export(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        user = await register(api_client)
        doomed, kept = await occurrences(api_client, llm_provider, user, 2, 3)

        await delete(api_client, user, doomed["id"])

        export = (await api_client.get("/api/v1/account/export", headers=user.headers)).json()
        assert [event["id"] for event in export["data"]["events"]] == [kept["id"]]
        assert [memory["id"] for memory in export["data"]["memories"]] == [kept["memory_id"]]

    async def test_a_malformed_id_is_a_validation_error(self, api_client: AsyncClient):
        user = await register(api_client)

        response = await delete(api_client, user, "not-a-uuid")

        assert_validation_error(response, "path.event_id")


class TestRepeatedDeletion:
    async def test_deleting_twice_reports_not_found_the_second_time(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)

        first = await delete(api_client, user, event_id)
        second = await delete(api_client, user, event_id)

        assert first.status_code == 204
        assert second.status_code == 404
        assert second.json()["error"]["code"] == "EVENT_NOT_FOUND"
        assert second.json()["meta"]["request_id"]

    async def test_a_second_attempt_removes_nothing_else(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user, days_ago=1)
        sibling = await standard_event(db_session, user, days_ago=2)
        await delete(api_client, user, event_id)
        after_first = await event_count(db_session)

        for _ in range(3):
            await delete(api_client, user, event_id)

        assert await event_count(db_session) == after_first
        assert await event_row(db_session, sibling) is not None

    async def test_two_deletes_at_once_succeed_exactly_once(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """The second waits on the first's row lock, then finds nothing to delete."""
        user = await register(api_client)
        event_id = await standard_event(db_session, user, days_ago=1)
        sibling = await standard_event(db_session, user, days_ago=2)

        responses = await asyncio.gather(*(delete(api_client, user, event_id) for _ in range(2)))

        assert sorted(response.status_code for response in responses) == [204, 404]
        assert await event_row(db_session, sibling) is not None

    async def test_an_id_that_never_existed_reports_not_found(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        await standard_event(db_session, user)
        before = await event_count(db_session)

        response = await delete(api_client, user, uuid.uuid4())

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "EVENT_NOT_FOUND"
        assert await event_count(db_session) == before

    async def test_an_event_removed_with_its_memory_reports_not_found(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        user = await register(api_client)
        [event] = await occurrences(api_client, llm_provider, user, 2)
        await api_client.delete(f"{MEMORIES}/{event['memory_id']}", headers=user.headers)

        response = await delete(api_client, user, event["id"])

        assert response.status_code == 404


class TestCrossUserAttempts:
    async def test_another_users_event_reports_exactly_what_a_missing_one_does(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """Nothing in the answer may tell a caller that the id exists."""
        ada = await register(api_client, "ada@example.com")
        grace = await register(api_client, "grace@example.com")
        event_id = await standard_event(db_session, ada)

        foreign = await delete(api_client, grace, event_id)
        missing = await delete(api_client, grace, uuid.uuid4())

        assert foreign.status_code == missing.status_code == 404
        assert foreign.json()["error"] == missing.json()["error"]

    async def test_another_users_event_survives_untouched(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        ada = await register(api_client, "ada@example.com")
        grace = await register(api_client, "grace@example.com")
        event_id = await standard_event(db_session, ada)
        before = await event_row(db_session, event_id)
        graces_own = await standard_event(db_session, grace)

        await delete(api_client, grace, event_id)

        assert await event_row(db_session, event_id) == before
        assert await event_row(db_session, graces_own) is not None
        assert await totals(db_session, ada) == {("study", "hours"): (1, 2.0, 0)}

    async def test_the_owner_can_still_delete_after_a_foreign_attempt(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        ada = await register(api_client, "ada@example.com")
        grace = await register(api_client, "grace@example.com")
        event_id = await standard_event(db_session, ada)
        await delete(api_client, grace, event_id)

        response = await delete(api_client, ada, event_id)

        assert response.status_code == 204

    async def test_a_foreign_attempt_leaves_no_lock_behind(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """A refused delete must not hold the row, or the owner's next edit would hang."""
        ada = await register(api_client, "ada@example.com")
        grace = await register(api_client, "grace@example.com")
        event_id = await standard_event(db_session, ada)

        await delete(api_client, grace, event_id)

        async with sessions_like(db_session)() as probe:
            locked = await probe.scalar(
                select(Event.id).where(Event.id == event_id).with_for_update(nowait=True)
            )
            await probe.rollback()
        assert locked == event_id

    @pytest.mark.parametrize(
        "headers",
        [{}, {"Authorization": "Bearer not-a-token"}, {"Authorization": "Basic abc"}],
    )
    async def test_a_valid_token_is_required(
        self, api_client: AsyncClient, db_session: AsyncSession, headers: dict[str, str]
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)

        response = await api_client.delete(f"{EVENTS}/{event_id}", headers=headers)

        assert response.status_code == 401
        assert await event_row(db_session, event_id) is not None

    async def test_a_deactivated_account_cannot_delete(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)
        await db_session.execute(update(User).where(User.id == user.id).values(is_active=False))
        await db_session.commit()

        response = await delete(api_client, user, event_id)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INACTIVE_USER"
        assert await event_row(db_session, event_id) is not None


class TestParentMemory:
    async def test_the_memory_is_kept_exactly_as_it_was(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        user = await register(api_client)
        doomed, _ = await occurrences(api_client, llm_provider, user, 2, 3)
        before = await memory_state(db_session, doomed["memory_id"])
        [listed_before] = (await api_client.get(MEMORIES, headers=user.headers)).json()["data"]

        await delete(api_client, user, doomed["id"])

        assert await memory_state(db_session, doomed["memory_id"]) == before
        [listed_after] = (await api_client.get(MEMORIES, headers=user.headers)).json()["data"]
        assert listed_after == listed_before

    async def test_deleting_a_memorys_only_event_keeps_the_memory(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """What was said stays remembered; only the countable record goes."""
        user = await register(api_client)
        [event] = await occurrences(api_client, llm_provider, user, 2)
        before = await memory_state(db_session, event["memory_id"])

        await delete(api_client, user, event["id"])

        assert await memory_state(db_session, event["memory_id"]) == before
        assert (await api_client.get(MEMORIES, headers=user.headers)).json()["meta"][
            "total_items"
        ] == 1

    async def test_the_memory_can_still_be_managed_afterwards(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        user = await register(api_client)
        doomed, kept = await occurrences(api_client, llm_provider, user, 2, 3)
        await delete(api_client, user, doomed["id"])
        url = f"{MEMORIES}/{kept['memory_id']}"

        edited = await api_client.patch(url, headers=user.headers, json={"content": "Studied"})
        deleted = await api_client.delete(url, headers=user.headers)

        assert edited.status_code == 200
        assert deleted.status_code == 204
        # The memory takes the occurrence it still had, as deleting a memory always did.
        assert await event_count(db_session) == 0


class TestSiblingEvents:
    async def test_the_other_occurrences_of_the_memory_are_kept(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        user = await register(api_client)
        first, middle, last = await occurrences(api_client, llm_provider, user, 1, 2, 3)
        sibling_rows = [await event_row(db_session, item["id"]) for item in (first, last)]

        await delete(api_client, user, middle["id"])

        assert [await event_row(db_session, item["id"]) for item in (first, last)] == (sibling_rows)
        remaining = (await listed(api_client, user))["data"]
        assert {item["id"] for item in remaining} == {first["id"], last["id"]}
        assert {item["memory_id"] for item in remaining} == {middle["memory_id"]}

    async def test_siblings_survive_one_deletion_after_another(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        user = await register(api_client)
        first, middle, last = await occurrences(api_client, llm_provider, user, 1, 2, 3)
        memory_before = await memory_state(db_session, middle["memory_id"])

        await delete(api_client, user, first["id"])
        await delete(api_client, user, last["id"])

        assert [item["id"] for item in (await listed(api_client, user))["data"]] == [middle["id"]]
        assert await memory_state(db_session, middle["memory_id"]) == memory_before

    async def test_a_sibling_can_still_be_edited_and_deleted(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        user = await register(api_client)
        doomed, kept = await occurrences(api_client, llm_provider, user, 2, 3)
        await delete(api_client, user, doomed["id"])

        edited = await patch(api_client, user, kept["id"], {"quantity": 4})
        deleted = await delete(api_client, user, kept["id"])

        assert edited.status_code == 200
        assert deleted.status_code == 204


class TestAggregation:
    async def test_the_sum_drops_by_exactly_the_deleted_amount(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        doomed = await standard_event(db_session, user, quantity=2, days_ago=1)
        await standard_event(db_session, user, quantity=3, days_ago=2)
        assert await totals(db_session, user) == {("study", "hours"): (2, 5.0, 0)}

        await delete(api_client, user, doomed)

        assert await totals(db_session, user) == {("study", "hours"): (1, 3.0, 0)}

    async def test_deleting_a_reinforced_occurrence_keeps_the_rest_counted(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider, db_session: AsyncSession
    ):
        """Deleting the memory would zero this total; deleting one event must not."""
        user = await register(api_client)
        _, middle, _ = await occurrences(api_client, llm_provider, user, 1, 2, 4)

        await delete(api_client, user, middle["id"])

        assert await totals(db_session, user) == {("study", "hours"): (2, 5.0, 0)}

    async def test_the_last_event_of_a_group_removes_the_group(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        await standard_event(db_session, user, quantity=2)
        only_reading = await standard_event(
            db_session, user, activity="reading", quantity=30, unit="pages"
        )

        await delete(api_client, user, only_reading)

        assert await totals(db_session, user) == {("study", "hours"): (1, 2.0, 0)}
        assert await totals(db_session, user, activities=("reading",)) == {}

    async def test_an_occurrence_without_an_amount_leaves_the_count(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        untimed = await standard_event(db_session, user, quantity=None, unit=None)
        await standard_event(db_session, user, quantity=None, unit=None, days_ago=1)
        await standard_event(db_session, user, quantity=2)

        await delete(api_client, user, untimed)

        assert await totals(db_session, user) == {
            ("study", "hours"): (1, 2.0, 0),
            ("study", None): (1, None, 1),
        }

    async def test_only_the_period_the_event_was_in_changes(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        zone = "Pacific/Kiritimati"
        user = await register(api_client, timezone=zone)
        today = await standard_event(db_session, user, zone, quantity=2, days_ago=0)
        await standard_event(db_session, user, zone, quantity=3, days_ago=3)

        await delete(api_client, user, today)

        assert await totals(db_session, user, TimeRangeSpec.TODAY, zone) == {}
        assert await totals(db_session, user, TimeRangeSpec.LAST_7_DAYS, zone) == {
            ("study", "hours"): (1, 3.0, 0)
        }

    async def test_a_refused_delete_changes_no_total(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        ada = await register(api_client, "ada@example.com")
        grace = await register(api_client, "grace@example.com")
        event_id = await standard_event(db_session, ada)
        before = await totals(db_session, ada)

        await delete(api_client, grace, event_id)
        await delete(api_client, ada, uuid.uuid4())

        assert await totals(db_session, ada) == before

    async def test_the_timeline_leaves_it_out(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        doomed = await standard_event(db_session, user, days_ago=1)
        await standard_event(db_session, user, days_ago=2)

        await delete(api_client, user, doomed)

        entries = await TemporalEventRetriever(sessions_like(db_session)).timeline(
            user.id, resolve_range(TimeRangeSpec.LAST_7_DAYS, datetime.now(tz=UTC))
        )
        assert len(entries) == 1

    async def test_the_very_next_chat_answers_from_the_reduced_total(
        self,
        api_client: AsyncClient,
        llm_provider: FakeLLMProvider,
    ):
        """End to end and immediate: nothing between the delete and the answer is cached."""
        user = await register(api_client)
        doomed, _ = await occurrences(api_client, llm_provider, user, 2, 3)
        before = await total_in_prompt(api_client, llm_provider, user, TimeRangeSpec.LAST_7_DAYS)
        assert "5 hours" in before

        await delete(api_client, user, doomed["id"])
        after = await total_in_prompt(api_client, llm_provider, user, TimeRangeSpec.LAST_7_DAYS)

        assert "1 occurrence(s)" in after
        assert "3 hours" in after


class TestTransactions:
    async def test_a_delete_waits_for_an_edit_holding_the_row_and_then_applies(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        """The delete and a concurrent edit serialise on the row; neither half-applies."""
        user = await register(api_client)
        event_id = await standard_event(db_session, user)

        async with sessions_like(db_session)() as editor:
            await editor.execute(select(Event).where(Event.id == event_id).with_for_update())
            pending = asyncio.create_task(delete(api_client, user, event_id))
            await asyncio.sleep(0.3)
            assert not pending.done(), "the delete did not wait for the row lock"
            await editor.rollback()

        response = await asyncio.wait_for(pending, timeout=5)
        assert response.status_code == 204
        assert await event_row(db_session, event_id) is None

    async def test_an_edit_racing_a_delete_never_resurrects_the_event(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        user = await register(api_client)
        event_id = await standard_event(db_session, user)

        edit, removal = await asyncio.gather(
            patch(api_client, user, event_id, {"quantity": 9}),
            delete(api_client, user, event_id),
        )

        assert removal.status_code == 204
        assert edit.status_code in {200, 404}
        assert await event_row(db_session, event_id) is None

    async def test_a_delete_landing_the_moment_an_edit_commits_does_not_fail_the_edit(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The exact interleaving the racing test above can only hit by chance.

        The delete waits on the edit's row lock and runs the instant the edit
        commits. The edit used to read its updated_at back after committing,
        found the row gone, and answered 500 for a change it had saved. Here the
        delete is run inside the edit's commit, so the window is hit every time.
        """
        user = await register(api_client)
        event_id = await standard_event(db_session, user)
        sessions = sessions_like(db_session)

        async with sessions() as session:
            committed = session.commit

            async def commit_then_delete() -> None:
                await committed()
                async with sessions() as other:
                    await event_service(other).delete(event_id, user.id)

            monkeypatch.setattr(session, "commit", commit_then_delete)
            result = await event_service(session).update(
                event_id, user.id, UpdateEventCommand(quantity=9)
            )

        # The edit happened, and is reported as it was saved; the delete came after.
        assert (result.event.quantity, result.event.unit) == (9, "hours")
        assert result.event.updated_at is not None
        assert await event_row(db_session, event_id) is None

    async def test_nothing_references_an_event_so_deleting_one_cannot_cascade(
        self, db_session: AsyncSession
    ):
        """Pins the schema the endpoint's guarantees rest on.

        An event is only ever the child: its memory and conversation links null
        out from the parent's side, and its owner's deletion cascades down to
        it. A future foreign key or trigger pointing at events would change
        what deleting one does, and should have to change this test too.
        """
        rows = await db_session.execute(
            text(
                """
                SELECT conrelid::regclass::text, confrelid::regclass::text, confdeltype::text
                FROM pg_constraint
                WHERE contype = 'f'
                  AND (conrelid = 'events'::regclass OR confrelid = 'events'::regclass)
                """
            )
        )
        triggers = await db_session.scalar(
            text(
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgrelid = 'events'::regclass AND NOT tgisinternal"
            )
        )

        assert {tuple(row) for row in rows} == {
            ("events", "users", "c"),
            ("events", "memories", "n"),
            ("events", "conversations", "n"),
        }
        assert triggers == 0
