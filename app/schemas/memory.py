import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.domain.memory.types import (
    MemoryDurability,
    MemorySource,
    MemoryStatus,
    MemoryType,
)

MemoryContent = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]


class MemoryResponse(BaseModel):
    id: uuid.UUID
    content: str
    memory_type: MemoryType
    status: MemoryStatus
    durability: MemoryDurability
    source: MemorySource

    importance: float
    confidence: float

    # Lets users see and act on what was recorded about them.
    is_sensitive: bool

    occurred_at: datetime | None = None
    expires_at: datetime | None = None
    superseded_by_id: uuid.UUID | None = None

    created_at: datetime
    updated_at: datetime


class UpdateMemoryRequest(BaseModel):
    """A partial update. Absent fields are left unchanged."""

    content: MemoryContent | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)

    # Clients may only archive or restore; superseding is the system's call.
    status: MemoryStatus | None = None

    @model_validator(mode="after")
    def _reject_empty_update(self) -> "UpdateMemoryRequest":
        if self.content is None and self.importance is None and self.status is None:
            raise ValueError("at least one field must be supplied")
        return self

    @model_validator(mode="after")
    def _reject_asserted_supersession(self) -> "UpdateMemoryRequest":
        if self.status is MemoryStatus.SUPERSEDED:
            raise ValueError("status cannot be set to superseded directly")
        return self
