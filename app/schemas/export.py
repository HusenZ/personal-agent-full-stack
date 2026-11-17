"""The shape of a user's data export.

Every field below is named on purpose: the document is built from these models
rather than by dumping rows, so a column added to a table later is not exported
until someone decides it should be. That is what keeps the password hash and
refresh tokens out by construction rather than by exclusion.

Columns stored as JSON are passed through as any JSON value rather than given a
shape. Their shape has followed the workflows that write them and will again;
an export must hand back whatever an old row holds, not fail on it.
"""

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.memory.types import MemoryChange, MemoryStatus
from app.domain.subscription.plans import Feature, Plan
from app.infrastructure.database.models import OnboardingStatus
from app.schemas.conversation import MessageResponse
from app.schemas.memory import MemoryResponse
from app.schemas.profile import ProfileResponse

# Bumped when the document's shape changes in a way a reader must know about.
EXPORT_FORMAT_VERSION: Literal[1] = 1


class ExportedAccountResponse(BaseModel):
    id: uuid.UUID
    email: str
    created_at: datetime


class ExportedProfileResponse(ProfileResponse):
    created_at: datetime
    updated_at: datetime


class ExportedOnboardingSessionResponse(BaseModel):
    id: uuid.UUID
    status: OnboardingStatus
    questions_asked: int
    overall_confidence: float
    state: Any = Field(
        description="The interview as stored: questions asked and what was collected."
    )
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ExportedAnalysisResponse(BaseModel):
    id: uuid.UUID
    overall_confidence: float
    degraded: bool
    claims: Any = Field(description="The analysis's claims as stored.")
    evidence_snapshot: Any = Field(
        description="The evidence the claims cite, as it was when the analysis was made."
    )
    prompt_name: str
    prompt_version: str
    generation_attempts: int
    rejected_claim_count: int
    created_at: datetime
    updated_at: datetime


class ExportedConversationResponse(BaseModel):
    id: uuid.UUID
    title: str
    message_count: int
    created_at: datetime
    last_message_at: datetime
    messages: list[MessageResponse] = Field(description="Every message, in order.")


class ExportedMemoryVersionResponse(BaseModel):
    id: uuid.UUID
    change: MemoryChange
    content: str
    status: MemoryStatus
    importance: float
    confidence: float
    reason: str | None
    created_at: datetime


class ExportedMemoryResponse(MemoryResponse):
    """A memory as /memories returns it, plus where it came from and its history.

    The embedding vector is left out: it is derived from ``content`` by a named
    model, holds nothing the text does not, and would make the document many
    times larger.
    """

    conversation_id: uuid.UUID | None
    versions: list[ExportedMemoryVersionResponse] = Field(
        description="Every recorded change to this memory, oldest first."
    )


class ExportedEventResponse(BaseModel):
    id: uuid.UUID
    activity: str
    description: str
    occurred_at: datetime
    quantity: float | None
    unit: str | None
    conversation_id: uuid.UUID | None
    memory_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ExportedWeekPlanResponse(BaseModel):
    id: uuid.UUID
    starts_on: date
    timezone: str
    summary: str
    blocks: Any = Field(description="The plan's blocks as stored.")
    evidence_snapshot: Any = Field(description="The evidence the plan cites, as stored.")
    prompt_name: str
    prompt_version: str
    rejected_block_count: int
    created_at: datetime
    updated_at: datetime


class ExportedSubscriptionResponse(BaseModel):
    """The plan grant as recorded, whether or not it has since expired.

    /subscription reports the plan in effect today; this is the underlying row.
    """

    plan: Plan
    source: str
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ExportedUsageCounterResponse(BaseModel):
    feature: Feature
    period_start: date
    used: int


class AccountExportResponse(BaseModel):
    format_version: Literal[1] = Field(
        default=EXPORT_FORMAT_VERSION,
        description="The document's shape. Bumped when a saved copy needs reading differently.",
    )
    exported_at: datetime

    account: ExportedAccountResponse
    profile: ExportedProfileResponse | None
    onboarding_sessions: list[ExportedOnboardingSessionResponse]
    analyses: list[ExportedAnalysisResponse]
    conversations: list[ExportedConversationResponse]
    memories: list[ExportedMemoryResponse] = Field(
        description="Every memory in every status: active, superseded and archived."
    )
    events: list[ExportedEventResponse]
    week_plans: list[ExportedWeekPlanResponse]
    subscription: ExportedSubscriptionResponse | None = Field(
        description="Null for an account that has never been granted a paid plan."
    )
    usage_counters: list[ExportedUsageCounterResponse] = Field(
        description="Metered feature use, one entry per feature per monthly period."
    )
