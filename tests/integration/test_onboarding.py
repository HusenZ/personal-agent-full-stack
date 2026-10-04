from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.onboarding.context import (
    ExtractedConstraint,
    ExtractedGoal,
    ExtractedObservation,
    ExtractedPreference,
    ExtractionResult,
)
from app.infrastructure.database.models import OnboardingSession, OnboardingStatus
from app.infrastructure.llm.prompts.onboarding import OPENING_QUESTION
from tests.fakes import FakeLLMProvider

pytestmark = pytest.mark.integration

PASSWORD = "a-sufficiently-long-password"


def rich_extraction() -> ExtractionResult:
    return ExtractionResult(
        goals=[
            ExtractedGoal(name="Move into AI engineering", priority="high", confidence=0.95),
            ExtractedGoal(name="Ship a backend", priority="medium", confidence=0.9),
        ],
        constraints=[ExtractedConstraint(description="Only evenings free", confidence=0.9)],
        preferences=[
            ExtractedPreference(key="communication_style", value="direct", confidence=0.9)
        ],
        observations=[
            ExtractedObservation(content="Flutter developer today", confidence=0.9),
            ExtractedObservation(content="Has shipped production apps", confidence=0.9),
        ],
    )


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(
        extractions=[rich_extraction() for _ in range(15)],
        questions=[f"Generated question {index}" for index in range(15)],
    )


async def authenticate(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    access_token: str = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


async def run_interview(
    client: AsyncClient, headers: dict[str, str], max_turns: int = 20
) -> dict[str, Any]:
    """Answer questions until onboarding reports completion."""
    turn: dict[str, Any] = (await client.post("/api/v1/onboarding/start", headers=headers)).json()[
        "data"
    ]

    for _ in range(max_turns):
        if turn["is_complete"]:
            return turn
        response = await client.post(
            "/api/v1/onboarding/message",
            headers=headers,
            json={"answer": "Here is a detailed and informative answer."},
        )
        turn = response.json()["data"]

    raise AssertionError("onboarding did not complete within the turn budget")


class TestAuthorization:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/v1/onboarding/start"),
            ("post", "/api/v1/onboarding/message"),
            ("get", "/api/v1/onboarding/status"),
        ],
    )
    async def test_every_endpoint_requires_a_token(
        self, api_client: AsyncClient, method: str, path: str
    ):
        response = await api_client.request(method, path, json={"answer": "x"})

        assert response.status_code == 401


class TestStart:
    async def test_opens_with_the_fixed_question(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.post("/api/v1/onboarding/start", headers=headers)

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["question"]["text"] == OPENING_QUESTION
        assert data["questions_asked"] == 1
        assert data["is_complete"] is False

    async def test_starting_twice_resumes_rather_than_restarting(self, api_client: AsyncClient):
        """A retried request must not discard answers already collected."""
        headers = await authenticate(api_client, "ada@example.com")
        first = (await api_client.post("/api/v1/onboarding/start", headers=headers)).json()["data"]
        await api_client.post(
            "/api/v1/onboarding/message", headers=headers, json={"answer": "An answer"}
        )

        resumed = (await api_client.post("/api/v1/onboarding/start", headers=headers)).json()[
            "data"
        ]

        assert resumed["session_id"] == first["session_id"]
        assert resumed["questions_asked"] == 2

    async def test_only_one_session_exists_after_concurrent_starts(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")

        for _ in range(3):
            await api_client.post("/api/v1/onboarding/start", headers=headers)

        sessions = (await db_session.execute(select(OnboardingSession))).scalars().all()
        assert len(sessions) == 1


class TestMessage:
    async def test_answering_advances_the_interview(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await api_client.post("/api/v1/onboarding/start", headers=headers)

        response = await api_client.post(
            "/api/v1/onboarding/message",
            headers=headers,
            json={"answer": "I want to move into AI engineering."},
        )

        data = response.json()["data"]
        assert data["questions_asked"] == 2
        assert data["overall_confidence"] > 0

    async def test_answering_before_starting_is_rejected(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.post(
            "/api/v1/onboarding/message", headers=headers, json={"answer": "Unprompted"}
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "ONBOARDING_NOT_STARTED"

    @pytest.mark.parametrize("answer", ["", "   ", "x" * 4001])
    async def test_invalid_answers_are_rejected(self, api_client: AsyncClient, answer: str):
        headers = await authenticate(api_client, "ada@example.com")
        await api_client.post("/api/v1/onboarding/start", headers=headers)

        response = await api_client.post(
            "/api/v1/onboarding/message", headers=headers, json={"answer": answer}
        )

        assert response.status_code == 422

    async def test_state_survives_between_requests(self, api_client: AsyncClient):
        """Each turn is a separate HTTP request; the interview must not reset."""
        headers = await authenticate(api_client, "ada@example.com")
        await api_client.post("/api/v1/onboarding/start", headers=headers)

        for expected in (2, 3, 4):
            response = await api_client.post(
                "/api/v1/onboarding/message", headers=headers, json={"answer": "An answer"}
            )
            assert response.json()["data"]["questions_asked"] == expected


class TestCompletion:
    async def test_the_interview_completes_and_writes_the_profile(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        final = await run_interview(api_client, headers)

        assert final["is_complete"] is True
        assert final["status"] == "completed"
        assert final["question"] is None

        profile = (await api_client.get("/api/v1/profile", headers=headers)).json()["data"]
        assert [goal["name"] for goal in profile["goals"]] == [
            "Move into AI engineering",
            "Ship a backend",
        ]
        assert profile["constraints"] == ["Only evenings free"]
        assert profile["preferences"] == {"communication_style": "direct"}

    async def test_completion_is_recorded_on_the_session(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await run_interview(api_client, headers)

        onboarding = (await db_session.execute(select(OnboardingSession))).scalar_one()

        assert onboarding.status is OnboardingStatus.COMPLETED
        assert onboarding.completed_at is not None
        assert onboarding.overall_confidence > 0

    async def test_answering_after_completion_is_rejected(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await run_interview(api_client, headers)

        response = await api_client.post(
            "/api/v1/onboarding/message", headers=headers, json={"answer": "One more thing"}
        )

        assert response.status_code == 404

    async def test_restarting_after_completion_is_rejected(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await run_interview(api_client, headers)

        response = await api_client.post("/api/v1/onboarding/start", headers=headers)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ONBOARDING_ALREADY_COMPLETED"


class TestStatus:
    async def test_status_before_starting_is_not_found(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.get("/api/v1/onboarding/status", headers=headers)

        assert response.status_code == 404

    async def test_status_reports_progress_without_advancing_it(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await api_client.post("/api/v1/onboarding/start", headers=headers)

        first = (await api_client.get("/api/v1/onboarding/status", headers=headers)).json()["data"]
        second = (await api_client.get("/api/v1/onboarding/status", headers=headers)).json()["data"]

        assert first == second
        assert first["questions_asked"] == 1
        assert first["question"]["text"] == OPENING_QUESTION

    async def test_status_exposes_the_confidence_breakdown(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await api_client.post("/api/v1/onboarding/start", headers=headers)
        await api_client.post(
            "/api/v1/onboarding/message", headers=headers, json={"answer": "An answer"}
        )

        data = (await api_client.get("/api/v1/onboarding/status", headers=headers)).json()["data"]

        assert set(data["coverage"]) == {"goal", "context", "constraint", "preference"}
        assert 0.0 <= data["overall_confidence"] <= 1.0


class TestUserIsolation:
    async def test_one_users_interview_is_invisible_to_another(self, api_client: AsyncClient):
        ada = await authenticate(api_client, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")
        await api_client.post("/api/v1/onboarding/start", headers=ada)

        response = await api_client.get("/api/v1/onboarding/status", headers=grace)

        assert response.status_code == 404

    async def test_sessions_do_not_collide_between_users(self, api_client: AsyncClient):
        ada = await authenticate(api_client, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")

        ada_start = (await api_client.post("/api/v1/onboarding/start", headers=ada)).json()["data"]
        grace_start = (await api_client.post("/api/v1/onboarding/start", headers=grace)).json()[
            "data"
        ]

        assert ada_start["session_id"] != grace_start["session_id"]

    async def test_completing_one_interview_does_not_touch_another_profile(
        self, api_client: AsyncClient
    ):
        ada = await authenticate(api_client, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")

        await run_interview(api_client, ada)

        grace_profile = (await api_client.get("/api/v1/profile", headers=grace)).json()["data"]
        assert grace_profile == {
            "timezone": "UTC",
            "goals": [],
            "constraints": [],
            "preferences": {},
        }
