import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.analysis.claims import AnalysisSection, ClaimType
from app.domain.analysis.evidence import EvidenceKind


class ClaimResponse(BaseModel):
    claim_type: ClaimType
    content: str
    confidence: float
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceResponse(BaseModel):
    id: str
    kind: EvidenceKind
    content: str


class AnalysisResponse(BaseModel):
    """An analysis, grouped by section with its evidence attached.

    The evidence is returned alongside the claims so a client can resolve every
    citation without a second request, which is what makes the claim types
    checkable by whoever reads them.
    """

    id: uuid.UUID
    created_at: datetime
    overall_confidence: float

    # True when returned below the quality bar, so clients can show caution.
    degraded: bool

    sections: dict[AnalysisSection, list[ClaimResponse]]
    evidence: list[EvidenceResponse]
