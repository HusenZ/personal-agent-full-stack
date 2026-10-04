from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

MemoryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Score = Annotated[float, Field(ge=0.0, le=1.0)]
ActivityLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=100)
]
UnitLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=32)
]

# Cap candidates per extraction; more means the model misread the task.
MAX_CANDIDATES = 12


class MemoryType(StrEnum):
    FACT = "fact"
    GOAL = "goal"
    PREFERENCE = "preference"
    CONSTRAINT = "constraint"
    EVENT = "event"
    EXPERIENCE = "experience"
    OBSERVATION = "observation"
    PLAN = "plan"
    TEMPORARY_CONTEXT = "temporary_context"


class MemoryDurability(StrEnum):
    """How long a memory is expected to stay true.

    Distinct from importance: "I have a dentist appointment on Friday" can matter
    a great deal this week and be worthless next week.
    """

    LONG_TERM = "long_term"
    SHORT_TERM = "short_term"
    TEMPORARY = "temporary"


class MemoryStatus(StrEnum):
    """Where a memory sits in its lifecycle.

    PRD 18 also lists UPDATED. It is not a state here: a memory that has been
    edited is still active, and modelling it as a status would give retrieval two
    different ways to mean "usable". The update is recorded on the version row
    instead, which is where the history belongs.
    """

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class MemorySource(StrEnum):
    CONVERSATION = "conversation"
    ONBOARDING = "onboarding"
    MANUAL = "manual"


class MemoryChange(StrEnum):
    """What happened to a memory, recorded once per version row."""

    CREATED = "created"
    UPDATED = "updated"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    REINFORCED = "reinforced"


class MemoryCandidate(BaseModel):
    """Something the model believes is worth remembering.

    Doubles as the structured-output schema, so the shape the model is
    constrained to and the shape the pipeline reasons about cannot drift apart.
    """

    content: MemoryText
    memory_type: MemoryType
    durability: MemoryDurability

    importance: Score = Field(description="How much this should shape future answers.")
    confidence: Score = Field(description="How clearly the exchange supports this.")

    # Flagged, not dropped: users can see and manage everything stored.
    is_sensitive: bool = Field(
        default=False,
        description="Health, finances, relationships, or anything similarly private.",
    )

    # Event-only fields that make an event countable.
    activity: ActivityLabel | None = Field(
        default=None,
        description="For events: what was done, as a single lowercase word such as 'study'.",
    )
    quantity: float | None = Field(
        default=None, ge=0, description="For events: how much, if the user said."
    )
    unit: UnitLabel | None = Field(
        default=None, description="For events: the unit of quantity, such as 'hours'."
    )

    # A day offset, not a date: models get date arithmetic wrong.
    days_ago: int | None = Field(
        default=None,
        ge=0,
        le=365,
        description="For events: how many days ago it happened. 0 means today.",
    )

    def is_countable_event(self) -> bool:
        return self.memory_type is MemoryType.EVENT and self.activity is not None


class MemoryExtraction(BaseModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list, max_length=MAX_CANDIDATES)
