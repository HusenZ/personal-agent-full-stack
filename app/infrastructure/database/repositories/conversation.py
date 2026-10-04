import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import Conversation, Message


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_owned_by(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID
    ) -> Conversation | None:
        """Load a conversation only if it belongs to this user.

        Ownership is part of the query rather than a check afterwards, so there
        is no path that reads a conversation first and authorises it second.
        """
        statement = select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def delete_owned_by(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Delete a conversation and its messages if it belongs to this user.

        A single statement with ownership in the WHERE clause, and the database's
        cascade removes the messages; loading them through the ORM first would
        read every message just to delete it. Returns whether anything was deleted.
        """
        statement = delete(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
        result = await self._session.execute(statement)
        return bool(getattr(result, "rowcount", 0))

    async def list_for_user(
        self, user_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Conversation], int]:
        total = await self._session.scalar(
            select(func.count()).select_from(Conversation).where(Conversation.user_id == user_id)
        )

        statement = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.last_message_at.desc(), Conversation.id.desc())
            .limit(limit)
            .offset(offset)
        )
        conversations = list((await self._session.execute(statement)).scalars().all())

        return conversations, int(total or 0)

    async def list_messages(
        self, conversation_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Message], int]:
        total = await self._session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )

        statement = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.position.asc())
            .limit(limit)
            .offset(offset)
        )
        messages = list((await self._session.execute(statement)).scalars().all())

        return messages, int(total or 0)

    async def recent_messages(self, conversation_id: uuid.UUID, limit: int) -> list[Message]:
        """The newest messages, returned oldest first.

        Selected newest first so the query is bounded by the index, then reversed
        so callers read the exchange in the order it happened.
        """
        statement = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.position.desc())
            .limit(limit)
        )
        messages = list((await self._session.execute(statement)).scalars().all())

        return list(reversed(messages))

    async def messages_owned_by(
        self, message_ids: list[uuid.UUID], user_id: uuid.UUID
    ) -> dict[uuid.UUID, Message]:
        """Load messages by id, only those in conversations this user owns."""
        statement = (
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Message.id.in_(message_ids), Conversation.user_id == user_id)
        )
        messages = (await self._session.execute(statement)).scalars().all()
        return {message.id: message for message in messages}

    def add(self, conversation: Conversation) -> None:
        self._session.add(conversation)

    def add_message(self, message: Message) -> None:
        self._session.add(message)
