import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.conversation.context import MessageRole
from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.mixins import TimestampMixin


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (
        # Conversations are always listed for one user, most recent first.
        Index("ix_conversations_user_id_last_message_at", "user_id", "last_message_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)

    # Separate from updated_at so renaming doesn't reorder the list.
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Denormalised so new positions need no count query.
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        # Never loaded implicitly; message reads are always paginated.
        lazy="raise",
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        # Reads are always "this conversation's messages in order".
        Index("ix_messages_conversation_id_position", "conversation_id", "position"),
        # Unique position also stops concurrent sends from interleaving.
        UniqueConstraint("conversation_id", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )

    # Explicit order: messages in one transaction share the same timestamp.
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, native_enum=False, length=20), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
