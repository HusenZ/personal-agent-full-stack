from fastapi import APIRouter, status

from app.api.dependencies import AnalysisServiceDep, CurrentUserDep
from app.api.rate_limiting import AnalysisRateLimit
from app.api.usage_limits import AnalysisUsage
from app.application.analysis.dto import AnalysisView
from app.core.context import get_request_id
from app.domain.analysis.claims import AnalysisSection
from app.schemas.analysis import AnalysisResponse, ClaimResponse, EvidenceResponse
from app.schemas.common import DataResponse, ResponseMeta

router = APIRouter(prefix="/analysis", tags=["analysis"])


def _to_response(view: AnalysisView) -> DataResponse[AnalysisResponse]:
    # Always include every section so clients get a stable shape.
    sections: dict[AnalysisSection, list[ClaimResponse]] = {
        section: [] for section in AnalysisSection
    }
    for claim in view.claims:
        sections[claim.section].append(
            ClaimResponse(
                claim_type=claim.claim_type,
                content=claim.content,
                confidence=round(claim.confidence, 4),
                evidence_ids=list(claim.evidence_ids),
            )
        )

    return DataResponse(
        data=AnalysisResponse(
            id=view.id,
            created_at=view.created_at,
            overall_confidence=round(view.overall_confidence, 4),
            degraded=view.degraded,
            sections=sections,
            evidence=[
                EvidenceResponse(id=item.id, kind=item.kind, content=item.content)
                for item in view.evidence.items
            ],
        ),
        meta=ResponseMeta(request_id=get_request_id()),
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[AnalysisResponse],
    summary="Generate an analysis from what is known about you",
)
async def generate_analysis(
    current_user: CurrentUserDep,
    service: AnalysisServiceDep,
    _limit: AnalysisRateLimit,
    _usage: AnalysisUsage,
) -> DataResponse[AnalysisResponse]:
    return _to_response(await service.generate(current_user.id))


@router.get(
    "/latest",
    response_model=DataResponse[AnalysisResponse],
    summary="Read the most recent analysis",
)
async def read_latest_analysis(
    current_user: CurrentUserDep, service: AnalysisServiceDep
) -> DataResponse[AnalysisResponse]:
    return _to_response(await service.get_latest(current_user.id))
