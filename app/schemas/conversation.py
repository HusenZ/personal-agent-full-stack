import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.domain.conversation.context import MessageRole

# Matches the prompt's untrusted-content budget; reject rather than truncate.
MessageText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
]


class ChatRequest(BaseModel):
    message: MessageText

    # Omit to start a new conversation; foreign ids return 404.
    conversation_id: uuid.UUID | None = None


class MessageResponse(BaseModel):
    id: uuid.UUID
    role: MessageRole
    content: str
    created_at: datetime


class ChatResponse(BaseModel):
    conversation_id: uuid.UUID
    title: str
    user_message: MessageResponse
    reply: MessageResponse

    # True when the reply is a fallback rather than a generated answer.
    degraded: bool = False

    # True/false from verification; null when not checked.
    grounded: bool | None = None

    # The run that produced the reply; its trace is at /api/v1/runs/{run_id}/trace.
    run_id: uuid.UUID | None = None


class ConversationSummaryResponse(BaseModel):
    id: uuid.UUID
    title: str
    message_count: int
    created_at: datetime
    last_message_at: datetime


class ConversationDetailResponse(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    last_message_at: datetime
    messages: list[MessageResponse] = Field(default_factory=list)
