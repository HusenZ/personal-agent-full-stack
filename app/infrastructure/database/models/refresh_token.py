import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base


class RevocationReason(StrEnum):
    ROTATED = "rotated"
    LOGOUT = "logout"
    REUSE_DETECTED = "reuse_detected"


class RefreshToken(Base):
    """A single issued refresh token.

    Tokens are chained into families: refreshing revokes the presented token as
    ROTATED and issues a successor in the same family. Presenting an already
    rotated token means the token leaked, so the whole family is revoked.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)

    # Only the hash is stored, so a DB leak doesn't leak usable tokens.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revocation_reason: Mapped[RevocationReason | None] = mapped_column(
        # VARCHAR + CHECK, so adding values is a simple migration.
        Enum(RevocationReason, native_enum=False, length=32),
        nullable=True,
    )
