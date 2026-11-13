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
from app.application.analysis.steps import describe_analysis_step
from app.application.runs.recording import build_run_record, store_failed_run
from app.core.context import get_request_id
from app.domain.analysis.claims import AnalysisClaim
from app.domain.analysis.evidence import EvidenceBundle, RawEvidence, build_evidence_bundle
from app.graph.analysis.graph import AnalysisGraph
from app.infrastructure.database.models import AgentRun, Analysis, RunKind
from app.infrastructure.database.repositories.analysis import AnalysisRepository
from app.infrastructure.database.repositories.onboarding import OnboardingSessionRepository
from app.infrastructure.database.repositories.profile import ProfileRepository
from app.infrastructure.database.repositories.run import RunRepository
from app.infrastructure.llm.prompts.analysis import build_analysis_prompt
from app.infrastructure.observability.tracing import RunTrace, bind_trace

logger = logging.getLogger(__name__)


class AnalysisService:
    """Generates and reads evidence-grounded analyses.

    Every graph run is recorded as a run of kind ``analysis``, readable through
    ``/api/v1/runs/{id}/trace`` like a conversation turn. A request refused for
    lack of evidence never starts the graph and so records no run.
    """

    def __init__(
        self,
        session: AsyncSession,
        analyses: AnalysisRepository,
        profiles: ProfileRepository,
        onboarding_sessions: OnboardingSessionRepository,
        runs: RunRepository,
        graph: AnalysisGraph,
    ) -> None:
        self._session = session
        self._analyses = analyses
        self._profiles = profiles
        self._onboarding_sessions = onboarding_sessions
        self._runs = runs
        self._graph = graph

    async def generate(self, user_id: uuid.UUID) -> AnalysisView:
        raw_evidence = await self._collect_evidence(user_id)

        # Nothing to cite means the analysis could only be invented.
        if build_evidence_bundle(raw_evidence).is_empty():
            raise InsufficientContextError()

        trace = RunTrace(run_id=uuid.uuid4(), kind=RunKind.ANALYSIS.value)
        with bind_trace(trace):
            try:
                return await self._run(user_id, raw_evidence, trace)
            except Exception as error:
                await self._record_failed_run(user_id, trace, error)
                raise

    async def _run(
        self, user_id: uuid.UUID, raw_evidence: RawEvidence, trace: RunTrace
    ) -> AnalysisView:
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
        self._runs.add(self._run_record(user_id, trace, result, error=None))

        # The analysis and the record of the run that made it, together or neither.
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
            run_id=trace.run_id,
        )

    async def _record_failed_run(
        self, user_id: uuid.UUID, trace: RunTrace, error: BaseException
    ) -> None:
        """Store the record of a run that failed, without an analysis.

        Best effort, as for conversation turns: when the database is what
        failed this fails too, and the original error is the one that matters.
        """
        record = self._run_record(user_id, trace, {}, error=error)
        await store_failed_run(self._session, self._runs, record)

    @staticmethod
    def _run_record(
        user_id: uuid.UUID,
        trace: RunTrace,
        result: dict[str, Any],
        *,
        error: BaseException | None,
    ) -> AgentRun:
        return build_run_record(
            trace,
            kind=RunKind.ANALYSIS,
            user_id=user_id,
            conversation_id=None,
            request_id=get_request_id(),
            result={
                "generation_attempts": result.get("attempt") or 0,
                "degraded": bool(result.get("degraded")),
            },
            error=error,
            describe=describe_analysis_step,
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
