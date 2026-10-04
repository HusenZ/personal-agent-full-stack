import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.analysis.claims import AnalysisClaim, AnalysisResult, AnalysisSection, ClaimType
from app.infrastructure.database.models import Analysis
from tests.fakes import FakeLLMProvider

pytestmark = pytest.mark.integration

PASSWORD = "a-sufficiently-long-password"


def claim(
    content: str,
    *,
    claim_type: ClaimType = ClaimType.FACT,
    confidence: float = 0.9,
    evidence_ids: list[str] | None = None,
    section: AnalysisSection = AnalysisSection.GOALS,
) -> AnalysisClaim:
    return AnalysisClaim(
        section=section,
        claim_type=claim_type,
        content=content,
        confidence=confidence,
        evidence_ids=evidence_ids if evidence_ids is not None else ["goal:1"],
    )


def good_analysis() -> AnalysisResult:
    return AnalysisResult(
        claims=[
            claim("Wants to move into AI engineering"),
            claim(
                "Evenings are the only available time",
                evidence_ids=["constraint:1"],
                section=AnalysisSection.CONSTRAINTS,
            ),
            claim(
                "Limited time is the main risk to the goal",
                claim_type=ClaimType.INFERENCE,
                confidence=0.6,
                evidence_ids=["constraint:1", "goal:1"],
                section=AnalysisSection.BOTTLENECKS,
            ),
        ]
    )


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(analyses=[good_analysis()])


async def authenticate(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    access_token: str = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


async def give_profile(client: AsyncClient, headers: dict[str, str]) -> None:
    await client.patch(
        "/api/v1/profile",
        headers=headers,
        json={
            "goals": [{"name": "Move into AI engineering", "priority": "high"}],
            "constraints": ["Only evenings free"],
            "preferences": {"communication_style": "direct"},
        },
    )


class TestAuthorization:
    @pytest.mark.parametrize(
        ("method", "path"), [("post", "/api/v1/analysis"), ("get", "/api/v1/analysis/latest")]
    )
    async def test_every_endpoint_requires_a_token(
        self, api_client: AsyncClient, method: str, path: str
    ):
        response = await api_client.request(method, path)

        assert response.status_code == 401


class TestInsufficientContext:
    async def test_an_empty_profile_is_refused(self, api_client: AsyncClient):
        """Nothing to cite means anything produced could only be invention."""
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.post("/api/v1/analysis", headers=headers)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INSUFFICIENT_CONTEXT"

    async def test_nothing_is_stored_when_refused(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await api_client.post("/api/v1/analysis", headers=headers)

        assert (await db_session.execute(select(Analysis))).scalars().all() == []

    async def test_no_model_call_is_made_when_refused(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        headers = await authenticate(api_client, "ada@example.com")

        await api_client.post("/api/v1/analysis", headers=headers)

        assert llm_provider.prompts_named("analysis.initial") == []


class TestGeneration:
    async def test_an_analysis_is_returned_grouped_by_section(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)

        response = await api_client.post("/api/v1/analysis", headers=headers)

        assert response.status_code == 201
        data = response.json()["data"]
        assert set(data["sections"]) == {section.value for section in AnalysisSection}
        assert len(data["sections"]["goals"]) == 1
        assert len(data["sections"]["bottlenecks"]) == 1
        assert data["sections"]["strengths"] == []

    async def test_every_claim_declares_how_strongly_it_is_asserted(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)

        data = (await api_client.post("/api/v1/analysis", headers=headers)).json()["data"]

        assert data["sections"]["goals"][0]["claim_type"] == "fact"
        assert data["sections"]["bottlenecks"][0]["claim_type"] == "inference"

    async def test_the_evidence_is_returned_so_citations_resolve(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)

        data = (await api_client.post("/api/v1/analysis", headers=headers)).json()["data"]

        evidence_ids = {item["id"] for item in data["evidence"]}
        for section in data["sections"].values():
            for claim_payload in section:
                assert set(claim_payload["evidence_ids"]) <= evidence_ids

    async def test_a_clean_generation_is_not_degraded(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)

        data = (await api_client.post("/api/v1/analysis", headers=headers)).json()["data"]

        assert data["degraded"] is False
        assert data["overall_confidence"] > 0

    async def test_onboarding_observations_become_citable_evidence(
        self, api_client: AsyncClient, llm_provider: FakeLLMProvider
    ):
        """Observations have no profile column yet, but they are still evidence."""
        from app.domain.onboarding.context import ExtractedObservation, ExtractionResult

        headers = await authenticate(api_client, "ada@example.com")
        llm_provider.queue_extractions(
            ExtractionResult(
                observations=[
                    ExtractedObservation(content="Flutter developer today", confidence=0.9)
                ]
            )
        )
        await api_client.post("/api/v1/onboarding/start", headers=headers)
        await api_client.post(
            "/api/v1/onboarding/message", headers=headers, json={"answer": "An answer"}
        )
        await give_profile(api_client, headers)

        data = (await api_client.post("/api/v1/analysis", headers=headers)).json()["data"]

        contents = {item["content"] for item in data["evidence"]}
        assert "Flutter developer today" in contents


class TestPersistence:
    async def test_the_analysis_is_stored_with_its_provenance(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)
        await api_client.post("/api/v1/analysis", headers=headers)

        analysis = (await db_session.execute(select(Analysis))).scalar_one()

        assert analysis.prompt_name == "analysis.initial"
        assert analysis.prompt_version
        assert analysis.generation_attempts == 1
        assert len(analysis.claims) == 3

    async def test_the_evidence_snapshot_is_stored(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)
        await api_client.post("/api/v1/analysis", headers=headers)

        analysis = (await db_session.execute(select(Analysis))).scalar_one()

        assert analysis.evidence_snapshot["goals"] == ["Move into AI engineering"]

    async def test_a_stored_analysis_keeps_citing_what_it_meant(self, api_client: AsyncClient):
        """Editing the profile afterwards must not silently rewrite past citations."""
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)
        await api_client.post("/api/v1/analysis", headers=headers)

        await api_client.patch(
            "/api/v1/profile",
            headers=headers,
            json={"goals": [{"name": "Something completely different", "priority": "low"}]},
        )
        data = (await api_client.get("/api/v1/analysis/latest", headers=headers)).json()["data"]

        goal_evidence = [item for item in data["evidence"] if item["id"] == "goal:1"]
        assert goal_evidence[0]["content"] == "Move into AI engineering"

    async def test_analyses_accumulate_rather_than_replace(
        self, api_client: AsyncClient, db_session: AsyncSession
    ):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)

        for _ in range(3):
            await api_client.post("/api/v1/analysis", headers=headers)

        analyses = (await db_session.execute(select(Analysis))).scalars().all()
        assert len(analyses) == 3


class TestLatest:
    async def test_no_analysis_yet_is_reported_as_not_found(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")

        response = await api_client.get("/api/v1/analysis/latest", headers=headers)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "ANALYSIS_NOT_FOUND"

    async def test_the_most_recent_analysis_is_returned(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)
        created = (await api_client.post("/api/v1/analysis", headers=headers)).json()["data"]

        latest = (await api_client.get("/api/v1/analysis/latest", headers=headers)).json()["data"]

        assert latest["id"] == created["id"]

    async def test_a_new_analysis_supersedes_the_previous_one(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)
        first = (await api_client.post("/api/v1/analysis", headers=headers)).json()["data"]
        second = (await api_client.post("/api/v1/analysis", headers=headers)).json()["data"]

        latest = (await api_client.get("/api/v1/analysis/latest", headers=headers)).json()["data"]

        assert first["id"] != second["id"]
        assert latest["id"] == second["id"]

    async def test_claim_types_survive_a_round_trip(self, api_client: AsyncClient):
        headers = await authenticate(api_client, "ada@example.com")
        await give_profile(api_client, headers)
        await api_client.post("/api/v1/analysis", headers=headers)

        data = (await api_client.get("/api/v1/analysis/latest", headers=headers)).json()["data"]

        assert data["sections"]["bottlenecks"][0]["claim_type"] == "inference"
        assert data["sections"]["goals"][0]["claim_type"] == "fact"


class TestUserIsolation:
    async def test_one_users_analysis_is_invisible_to_another(self, api_client: AsyncClient):
        ada = await authenticate(api_client, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")
        await give_profile(api_client, ada)
        await api_client.post("/api/v1/analysis", headers=ada)

        response = await api_client.get("/api/v1/analysis/latest", headers=grace)

        assert response.status_code == 404

    async def test_an_analysis_is_grounded_only_in_its_own_users_evidence(
        self, api_client: AsyncClient
    ):
        ada = await authenticate(api_client, "ada@example.com")
        grace = await authenticate(api_client, "grace@example.com")
        await give_profile(api_client, ada)
        await api_client.patch(
            "/api/v1/profile",
            headers=grace,
            json={"goals": [{"name": "Grace's private goal", "priority": "high"}]},
        )

        data = (await api_client.post("/api/v1/analysis", headers=ada)).json()["data"]

        contents = {item["content"] for item in data["evidence"]}
        assert "Grace's private goal" not in contents
