import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

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
    # Types the error was raised from, outermost first; its root cause is last.
    error_chain: list[str] | None = None
    # Where in the application code it surfaced: "app/<path>.py:<line> in <function>".
    error_location: str | None = None
    # Null when the run failed outside every node, such as while storing its result.
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
    # "ok", "failed", or "cancelled" when stopped from outside.
    status: str
    error_type: str | None
    # Defaults keep traces stored before these fields existed readable.
    error_chain: list[str] = Field(default_factory=list)
    error_location: str | None = None
    # Counts and outcomes allow-listed per step; never what was said or written.
    detail: dict[str, Any]
    model_calls: list[ModelCallResponse]


class RunTraceResponse(BaseModel):
    run: RunResponse
    steps: list[StepResponse]
