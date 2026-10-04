import uuid
from dataclasses import dataclass
from datetime import datetime

from app.domain.analysis.claims import AnalysisClaim
from app.domain.analysis.evidence import EvidenceBundle


@dataclass(frozen=True)
class AnalysisView:
    id: uuid.UUID
    created_at: datetime
    overall_confidence: float
    degraded: bool
    claims: tuple[AnalysisClaim, ...]
    evidence: EvidenceBundle
