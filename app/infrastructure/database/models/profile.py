import uuid
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.mixins import TimestampMixin


class GoalPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Profile(Base, TimestampMixin):
    """The structured model of a user.

    Currently a container for the collections below; onboarding and analysis add
    their own columns in later phases.
    """

    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    # Needed so "last week" means the user's week, not UTC's.
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )

    # Eager loading: always needed, and lazy loading fails under async.
    goals: Mapped[list["Goal"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Goal.position",
    )
    constraints: Mapped[list["Constraint"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Constraint.position",
    )
    preferences: Mapped[list["Preference"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Preference.key",
    )


class Goal(Base, TimestampMixin):
    __tablename__ = "goals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    priority: Mapped[GoalPriority] = mapped_column(
        Enum(GoalPriority, native_enum=False, length=16), nullable=False
    )
    # Keeps the order the user gave.
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    profile: Mapped[Profile] = relationship(back_populates="goals")


class Constraint(Base, TimestampMixin):
    __tablename__ = "constraints"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )

    description: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    profile: Mapped[Profile] = relationship(back_populates="constraints")


class Preference(Base, TimestampMixin):
    __tablename__ = "preferences"
    __table_args__ = (UniqueConstraint("profile_id", "key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )

    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    profile: Mapped[Profile] = relationship(back_populates="preferences")
