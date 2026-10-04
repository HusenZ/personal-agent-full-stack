import uuid
from datetime import datetime

from sqlalchemy import ColumnElement

from app.domain.memory.types import MemoryStatus
from app.infrastructure.database.models import Memory


def usable_memories(user_id: uuid.UUID, now: datetime) -> list[ColumnElement[bool]]:
    """The conditions every memory search must apply.

    Held in one place because each retriever applying its own would eventually
    disagree, and the disagreement would be a leak: one path recalling another
    user's memory, or a superseded belief, or temporary context that has expired.
    """
    return [
        Memory.user_id == user_id,
        Memory.status == MemoryStatus.ACTIVE,
        (Memory.expires_at.is_(None)) | (Memory.expires_at > now),
    ]
