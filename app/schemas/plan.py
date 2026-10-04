import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.domain.analysis.evidence import EvidenceKind
from app.domain.planning.week import BlockCategory


class PlanBlockResponse(BaseModel):
    id: str
    date: date
    start: str | None = Field(description="Local HH:MM, or null for any time that day.")
    starts_at: datetime | None = Field(
        description="The start as an instant with its UTC offset, or null for any time that day."
    )
    ends_at: datetime | None
    duration_minutes: int
    title: str
    category: BlockCategory
    reason: str
    evidence_ids: list[str]
    done: bool


class PlanEvidenceResponse(BaseModel):
    id: str
    kind: EvidenceKind
    content: str


class WeekPlanResponse(BaseModel):
    """Seven days of blocks, ordered by date then start time.

    Evidence is included so a client can show what each block's reason rests
    on without a second request.
    """

    id: uuid.UUID
    created_at: datetime
    starts_on: date
    ends_on: date
    timezone: str
    summary: str
    blocks: list[PlanBlockResponse]
    evidence: list[PlanEvidenceResponse]


class UpdatePlanBlockRequest(BaseModel):
    done: bool
