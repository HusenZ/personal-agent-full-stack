import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.infrastructure.database.models import RunKind, RunStatus


class RunResponse(BaseModel):
    id: uuid.UUID
    kind: RunKind
    status: RunStatus
    conversation_id: uuid.UUID | None
    request_id: str | None
    started_at: datetime
    duration_ms: float

    model_calls: int
    # Null when the provider reported no usage, which is not the same as zero.
    input_tokens: int | None
    output_tokens: int | None

    retrieval_attempts: int
    generation_attempts: int
    grounded: bool | None
    degraded: bool

    error_type: str | None
    failed_node: str | None


class ModelCallResponse(BaseModel):
    kind: str
    # The prompt's name and version, never its text.
    name: str
    version: str | None
    duration_ms: float
    status: str
    error_type: str | None
    input_tokens: int | None
    output_tokens: int | None


class StepResponse(BaseModel):
    node: str
    # Milliseconds from the start of the run, so steps can be laid out in time.
    offset_ms: float
    duration_ms: float
    status: str
    error_type: str | None
    # Counts and outcomes allow-listed per step; never what was said or written.
    detail: dict[str, Any]
    model_calls: list[ModelCallResponse]


class RunTraceResponse(BaseModel):
    run: RunResponse
    steps: list[StepResponse]
