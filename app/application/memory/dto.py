import uuid
from dataclasses import dataclass, field

from app.domain.memory.types import MemoryStatus, MemoryType
from app.infrastructure.database.models import Memory


@dataclass(frozen=True)
class ExtractionOutcome:
    """What one exchange changed about what is remembered."""

    created: int = 0
    reinforced: int = 0
    superseded: int = 0
    rejected: int = 0
    judged_pairs: int = 0


@dataclass(frozen=True)
class UpdateMemoryCommand:
    content: str | None = None
    importance: float | None = None
    status: MemoryStatus | None = None


@dataclass(frozen=True)
class MemoryPage:
    items: list[Memory] = field(default_factory=list)
    total_items: int = 0


@dataclass(frozen=True)
class MemoryFilters:
    statuses: tuple[MemoryStatus, ...] | None = None
    memory_type: MemoryType | None = None


@dataclass(frozen=True)
class ExtractionRequest:
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    user_message: str
    assistant_reply: str

    # Used to date events in the user's time zone, not the server's.
    timezone: str = "UTC"
