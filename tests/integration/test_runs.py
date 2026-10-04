"""Run records and traces end to end: real HTTP, real database, fake model."""

import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import AgentRun, Message, RunStatus
from app.retrieval.hybrid import HybridMemoryRetriever
from tests.fakes import FakeLLMProvider
from tests.integration.test_chat import authenticate
from tests.integration.test_chat_stream import parse

pytestmark = pytest.mark.integration

QUESTION = "How should I train for my half marathon?"
REPLY = "Build up your long run gradually and keep most runs easy."


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(replies=[REPLY])


async def chat(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.post("/api/v1/chat", headers=headers, json={"message": QUESTION})
    assert response.status_code == 201
    data: dict[str, Any] = response.json()["data"]
    return data


async def read(client: AsyncClient, headers: dict[str, str], path: str) -> Any:
    return await client.get(f"/api/v1/runs/{path}", headers=headers)


def break_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    async def lost_connection(self: Any, *args: Any, **kwargs: Any) -> list[Any]:
        raise ConnectionError("connection to 10.0.0.5 lost")

    monkeypatch.setattr(HybridMemoryRetriever, "retrieve", lost_connection)


class TestRunSummary:
    async def test_a_reply_names_the_run_that_produced_it(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        reply = await chat(api_client, headers)

        response = await read(api_client, headers, reply["run_id"])

        assert response.status_code == 200
        run = response.json()["data"]
        assert run["status"] == "completed"
        assert run["kind"] == "conversation_turn"
        assert run["conversation_id"] == reply["conversation_id"]
        assert run["generation_attempts"] == 1
        assert run["grounded"] is True
        assert run["request_id"]

    async def test_every_model_call_is_counted(self, api_client: AsyncClient):
        """A plan, a query embedding, a reply and its verification."""
        headers = await authenticate(api_client, "ada@example.com")
        reply = await chat(api_client, headers)

        run = (await read(api_client, headers, reply["run_id"])).json()["data"]

        assert run["model_calls"] == 4
        # The fakes report no usage, which must read as unknown rather than zero.
        assert run["input_tokens"] is None

    async def test_the_run_is_stored_with_the_exchange(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await chat(api_client, headers)

        assert await db_session.scalar(select(func.count()).select_from(AgentRun)) == 1
        assert await db_session.scalar(select(func.count()).select_from(Message)) == 2


class TestTrace:
    async def test_the_trace_lists_each_step_with_its_model_calls(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        reply = await chat(api_client, headers)

        trace = (await read(api_client, headers, f"{reply['run_id']}/trace")).json()["data"]

        assert [step["node"] for step in trace["steps"]] == [
            "understand_query",
            "retrieve_context",
            "build_context",
            "generate_reply",
            "verify_answer",
        ]
        verify = trace["steps"][-1]
        assert [call["name"] for call in verify["model_calls"]] == ["conversation.verify"]
        assert verify["detail"] == {"grounded": True}
        assert all(step["status"] == "ok" for step in trace["steps"])

    async def test_the_trace_holds_no_conversation_content(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        reply = await chat(api_client, headers)

        body = (await read(api_client, headers, f"{reply['run_id']}/trace")).text

        assert "half marathon" not in body
        assert "long run" not in body


class TestOwnership:
    async def test_another_users_run_is_not_found(self, api_client: AsyncClient):
        owner = await authenticate(api_client, "grace@example.com")
        run_id = (await chat(api_client, owner))["run_id"]
        intruder = await authenticate(api_client, "ada@example.com")

        for path in (run_id, f"{run_id}/trace"):
            response = await read(api_client, intruder, path)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "RUN_NOT_FOUND"

    async def test_a_run_that_never_existed_looks_identical(self, api_client: AsyncClient):
        owner = await authenticate(api_client, "grace@example.com")
        run_id = (await chat(api_client, owner))["run_id"]
        intruder = await authenticate(api_client, "ada@example.com")

        foreign = await read(api_client, intruder, run_id)
        missing = await read(api_client, intruder, str(uuid.uuid4()))

        assert foreign.status_code == missing.status_code == 404
        assert foreign.json()["error"] == missing.json()["error"]

    async def test_runs_require_a_token(self, api_client: AsyncClient):
        assert (await api_client.get(f"/api/v1/runs/{uuid.uuid4()}")).status_code == 401


class TestFailedRuns:
    async def test_a_failed_streamed_turn_leaves_a_failed_run(
        self, api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        headers = await authenticate(api_client, "ada@example.com")
        break_retrieval(monkeypatch)

        stream = await api_client.post(
            "/api/v1/chat/stream", headers=headers, json={"message": QUESTION}
        )
        events = parse(stream.text)
        run_id = events[0][1]["run_id"]
        assert events[-1][0] == "error"

        run = (await read(api_client, headers, run_id)).json()["data"]
        assert run["status"] == "failed"
        assert run["failed_node"] == "retrieve_context"
        assert run["error_type"] == "ConnectionError"
        # The conversation was created for this turn and rolled back with it.
        assert run["conversation_id"] is None

        trace = (await read(api_client, headers, f"{run_id}/trace")).json()["data"]
        assert [step["status"] for step in trace["steps"]] == ["ok", "failed"]
        assert "10.0.0.5" not in json.dumps(trace)

    async def test_a_failed_turn_stores_the_run_but_not_the_exchange(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        headers = await authenticate(api_client, "ada@example.com")
        break_retrieval(monkeypatch)

        with pytest.raises(ConnectionError):
            await api_client.post("/api/v1/chat", headers=headers, json={"message": QUESTION})

        runs = (await db_session.scalars(select(AgentRun))).all()
        assert [run.status for run in runs] == [RunStatus.FAILED]
        assert await db_session.scalar(select(func.count()).select_from(Message)) == 0

    async def test_a_failure_in_an_existing_conversation_keeps_the_link(
        self, api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        headers = await authenticate(api_client, "ada@example.com")
        conversation_id = (await chat(api_client, headers))["conversation_id"]
        break_retrieval(monkeypatch)

        stream = await api_client.post(
            "/api/v1/chat/stream",
            headers=headers,
            json={"message": QUESTION, "conversation_id": conversation_id},
        )
        run_id = parse(stream.text)[0][1]["run_id"]

        run = (await read(api_client, headers, run_id)).json()["data"]
        assert run["conversation_id"] == conversation_id


class TestStreaming:
    async def test_the_stream_names_its_run_from_the_first_event(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        stream = await api_client.post(
            "/api/v1/chat/stream", headers=headers, json={"message": QUESTION}
        )
        events = parse(stream.text)

        assert events[0][0] == "query_started"
        assert events[-1][1]["data"]["run_id"] == events[0][1]["run_id"]
        run = (await read(api_client, headers, events[0][1]["run_id"])).json()["data"]
        assert run["status"] == "completed"
