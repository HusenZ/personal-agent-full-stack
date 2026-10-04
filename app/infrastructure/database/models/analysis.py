import uuid
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Uuid, false, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.mixins import TimestampMixin


class Analysis(Base, TimestampMixin):
    """One generated analysis of a user.

    Analyses accumulate rather than replace: an earlier reading of a user stays
    meaningful, and comparing them over time is how the system's understanding
    can be shown to improve.
    """

    __tablename__ = "analyses"
    __table_args__ = (
        # Serves "latest analysis for a user"; also covers lookups by user.
        Index("ix_analyses_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Snapshot evidence so citations survive later profile edits.
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    claims: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    overall_confidence: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )

    # Prompt version, for comparing quality across prompt changes.
    prompt_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False)

    generation_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    rejected_claim_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
