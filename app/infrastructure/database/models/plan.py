import uuid
from datetime import date
from typing import Any

from sqlalchemy import Date, ForeignKey, Index, Integer, String, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.mixins import TimestampMixin


class WeekPlan(Base, TimestampMixin):
    """A generated plan for seven days.

    Blocks live in JSONB rather than a child table: a plan is read and written
    whole, a week holds a few dozen blocks at most, and nothing queries blocks
    across plans. Plans accumulate, like analyses, so a new plan never
    destroys what the user ticked off in the last one.
    """

    __tablename__ = "week_plans"
    __table_args__ = (Index("ix_week_plans_user_id_created_at", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # The first local date of the plan, in the time zone it was made for.
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    # Snapshotted so citations stay valid.
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    prompt_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False)
    rejected_block_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
