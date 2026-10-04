from dataclasses import dataclass

from app.infrastructure.database.models import GoalPriority


@dataclass(frozen=True)
class GoalInput:
    name: str
    priority: GoalPriority


@dataclass(frozen=True)
class UpdateProfileCommand:
    """A partial update.

    ``None`` means the collection was not supplied and is left untouched; a list
    replaces the collection wholesale. The two are distinct, so an empty list is
    a request to clear.
    """

    timezone: str | None = None
    goals: list[GoalInput] | None = None
    constraints: list[str] | None = None
    preferences: dict[str, str] | None = None
