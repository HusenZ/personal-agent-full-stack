import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.subscription.plans import Feature, Plan
from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.mixins import TimestampMixin


class Subscription(Base, TimestampMixin):
    """The plan a user has been granted.

    A user without a row is on the free plan, so registering needs no write
    here and only paying users have one.
    """

    __tablename__ = "subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    plan: Mapped[Plan] = mapped_column(Enum(Plan, native_enum=False, length=20), nullable=False)

    # Where the grant came from ("manual", later a billing provider).
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    # None for a grant with no end. Past it, the user is treated as free.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UsageCounter(Base):
    """How many units of one feature a user has used in one period.

    A counter rather than a count over the records each feature creates:
    deleting a conversation must not hand back the messages it used.
    """

    __tablename__ = "usage_counters"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    feature: Mapped[Feature] = mapped_column(
        Enum(Feature, native_enum=False, length=32), primary_key=True
    )
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
