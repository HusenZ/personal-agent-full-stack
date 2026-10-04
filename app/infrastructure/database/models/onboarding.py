import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, Integer, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.mixins import TimestampMixin


class OnboardingStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class OnboardingSession(Base, TimestampMixin):
    """One adaptive onboarding interview.

    The interview's working state is held as a JSON document rather than
    normalised columns. It is transient workflow state whose shape follows the
    graph, not a queryable business record: the durable outcome is written into
    the profile tables at completion. Phase 14 replaces this with LangGraph's own
    checkpointing, and normalised columns would have to be migrated away again.
    """

    __tablename__ = "onboarding_sessions"
    __table_args__ = (
        # At most one in-progress interview per user, enforced by the database.
        Index(
            "uq_onboarding_sessions_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'in_progress'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status: Mapped[OnboardingStatus] = mapped_column(
        Enum(OnboardingStatus, native_enum=False, length=32),
        nullable=False,
        default=OnboardingStatus.IN_PROGRESS,
        server_default=OnboardingStatus.IN_PROGRESS.value,
    )

    questions_asked: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    overall_confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )

    state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
