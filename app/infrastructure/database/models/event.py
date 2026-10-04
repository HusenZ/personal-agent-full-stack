import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.mixins import TimestampMixin


class Event(Base, TimestampMixin):
    """Something the user did, at a time, sometimes with an amount.

    Kept apart from semantic memory on purpose. A memory is retrieved by meaning
    and read by a model; an event is counted and summed by the database. Asking
    a model to add up study hours from retrieved sentences produces a number that
    looks right and frequently is not.
    """

    __tablename__ = "events"
    __table_args__ = (
        # Serves "user's events in a period", ordered by when they happened.
        Index("ix_events_user_id_occurred_at", "user_id", "occurred_at"),
        Index("ix_events_user_id_activity_occurred_at", "user_id", "activity", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Lowercase normalised so "studied" and "Study" aggregate together.
    activity: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Null when no amount was given; only events with both are summed.
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)

    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    memory_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("memories.id", ondelete="SET NULL"), nullable=True
    )
