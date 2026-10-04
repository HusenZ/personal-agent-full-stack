import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.analysis.dto import AnalysisView
from app.application.analysis.evidence_source import (
    collect_profile_evidence,
    deserialise_evidence,
    serialise_evidence,
)
from app.application.analysis.exceptions import AnalysisNotFoundError, InsufficientContextError
from app.domain.analysis.claims import AnalysisClaim
from app.domain.analysis.evidence import EvidenceBundle, RawEvidence, build_evidence_bundle
from app.graph.analysis.graph import AnalysisGraph
from app.infrastructure.database.models import Analysis
from app.infrastructure.database.repositories.analysis import AnalysisRepository
from app.infrastructure.database.repositories.onboarding import OnboardingSessionRepository
from app.infrastructure.database.repositories.profile import ProfileRepository
from app.infrastructure.llm.prompts.analysis import build_analysis_prompt

logger = logging.getLogger(__name__)


class AnalysisService:
    def __init__(
        self,
        session: AsyncSession,
        analyses: AnalysisRepository,
        profiles: ProfileRepository,
        onboarding_sessions: OnboardingSessionRepository,
        graph: AnalysisGraph,
    ) -> None:
        self._session = session
        self._analyses = analyses
        self._profiles = profiles
        self._onboarding_sessions = onboarding_sessions
        self._graph = graph

    async def generate(self, user_id: uuid.UUID) -> AnalysisView:
        raw_evidence = await self._collect_evidence(user_id)

        # Nothing to cite means the analysis could only be invented.
        if build_evidence_bundle(raw_evidence).is_empty():
            raise InsufficientContextError()

        result: dict[str, Any] = await self._graph.ainvoke(
            {"user_id": str(user_id), "raw_evidence": raw_evidence, "attempt": 0, "errors": []}
        )

        claims: list[AnalysisClaim] = list(result.get("claims") or [])
        evidence: EvidenceBundle = result.get("evidence") or build_evidence_bundle(raw_evidence)
        prompt = build_analysis_prompt(evidence)

        analysis = Analysis(
            user_id=user_id,
            evidence_snapshot=serialise_evidence(raw_evidence),
            claims=[claim.model_dump(mode="json") for claim in claims],
            overall_confidence=result.get("overall_confidence") or 0.0,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            generation_attempts=result.get("attempt") or 1,
            rejected_claim_count=len(result.get("rejected") or []),
            degraded=bool(result.get("degraded")),
        )
        self._analyses.add(analysis)
        await self._session.commit()

        logger.info(
            "analysis_generated",
            extra={
                "user_id": str(user_id),
                "analysis_id": str(analysis.id),
                "claim_count": len(claims),
                "rejected_claim_count": analysis.rejected_claim_count,
                "generation_attempts": analysis.generation_attempts,
                "overall_confidence": round(analysis.overall_confidence, 3),
                "degraded": analysis.degraded,
            },
        )

        return AnalysisView(
            id=analysis.id,
            created_at=analysis.created_at,
            overall_confidence=analysis.overall_confidence,
            degraded=analysis.degraded,
            claims=tuple(claims),
            evidence=evidence,
        )

    async def get_latest(self, user_id: uuid.UUID) -> AnalysisView:
        analysis = await self._analyses.get_latest_for_user(user_id)
        if analysis is None:
            raise AnalysisNotFoundError()

        return AnalysisView(
            id=analysis.id,
            created_at=analysis.created_at,
            overall_confidence=analysis.overall_confidence,
            degraded=analysis.degraded,
            claims=tuple(AnalysisClaim.model_validate(claim) for claim in analysis.claims),
            # Rebuild from the snapshot so stored citations stay valid.
            evidence=build_evidence_bundle(deserialise_evidence(analysis.evidence_snapshot)),
        )

    async def _collect_evidence(self, user_id: uuid.UUID) -> RawEvidence:
        return await collect_profile_evidence(user_id, self._profiles, self._onboarding_sessions)
