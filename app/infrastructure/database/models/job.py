import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.mixins import TimestampMixin


class JobKind(StrEnum):
    MEMORY_EXTRACTION = "memory_extraction"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    # Out of attempts. Kept rather than deleted so the failure stays visible.
    FAILED = "failed"


class BackgroundJob(Base, TimestampMixin):
    """Work to do after a request has been answered, held in the database.

    In PostgreSQL rather than Redis for two reasons. A job is written in the
    same transaction as the change that needs it, so an exchange can never be
    stored without its extraction job or the other way round. And this
    deployment's Redis keeps nothing across a restart by design, so a queue
    there would lose every waiting job whenever it restarted.
    """

    __tablename__ = "background_jobs"
    __table_args__ = (
        # Partial index for claiming the oldest due job.
        Index(
            "ix_background_jobs_claimable",
            "run_after",
            postgresql_where=text("status IN ('PENDING', 'RUNNING')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    kind: Mapped[JobKind] = mapped_column(Enum(JobKind, native_enum=False, length=50))
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=20), nullable=False, default=JobStatus.PENDING
    )

    # Ids only; the queue never stores a copy of user content.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)

    # Not before this time: now for a new job, later after a failure.
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Claims are leases; the token detects a superseded claim.
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # The exception type only, for the same reason run records keep only that.
    last_error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
