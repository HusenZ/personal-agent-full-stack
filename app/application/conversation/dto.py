import uuid
from dataclasses import dataclass, field

from app.infrastructure.database.models import Conversation, Message


@dataclass(frozen=True)
class ChatCommand:
    user_id: uuid.UUID
    message: str
    conversation_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ChatResult:
    conversation: Conversation
    user_message: Message
    reply: Message
    degraded: bool = False

    # None when the reply wasn't verified.
    grounded: bool | None = None

    # The run that produced this reply, whose trace can be read back.
    run_id: uuid.UUID | None = None

    # Passed along so extraction can date events in the user's time zone.
    timezone: str = "UTC"


@dataclass(frozen=True)
class Page[ItemT]:
    items: list[ItemT] = field(default_factory=list)
    total_items: int = 0


@dataclass(frozen=True)
class ConversationDetail:
    conversation: Conversation
    messages: list[Message] = field(default_factory=list)
    total_messages: int = 0
