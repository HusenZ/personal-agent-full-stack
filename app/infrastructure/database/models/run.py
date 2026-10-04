import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base


class RunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class RunKind(StrEnum):
    CONVERSATION_TURN = "conversation_turn"


class AgentRun(Base):
    """The durable record of one graph execution: what ran, for how long, at what cost.

    Holds metadata only. The question, the memories recalled and the drafts
    written all live elsewhere or nowhere; a run record that copied them would
    be a second, less protected store of the user's conversations.
    """

    __tablename__ = "agent_runs"
    __table_args__ = (
        # Runs are read one at a time by id, and listed per user newest first.
        Index("ix_agent_runs_user_id_started_at", "user_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Keep run records when a conversation is deleted.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )

    kind: Mapped[RunKind] = mapped_column(
        Enum(RunKind, native_enum=False, length=50), nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False, length=20), nullable=False
    )
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)

    model_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    # Null when no call reported usage, which is different from using none.
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    retrieval_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    grounded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    degraded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # Exception type and step only; messages may contain user data.
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failed_node: Mapped[str | None] = mapped_column(String(50), nullable=True)

    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
