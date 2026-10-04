from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.infrastructure.database.models import GoalPriority

GoalName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
ConstraintText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]
PreferenceKey = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
PreferenceValue = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]
TimezoneName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]

# Generous bounds that still cap writes and prompt size.
MAX_GOALS = 50
MAX_CONSTRAINTS = 50
MAX_PREFERENCES = 50


class GoalPayload(BaseModel):
    name: GoalName
    priority: GoalPriority


class GoalResponse(BaseModel):
    name: str
    priority: GoalPriority


class ProfileResponse(BaseModel):
    timezone: str
    goals: list[GoalResponse]
    constraints: list[str]
    preferences: dict[str, str]


class UpdateProfileRequest(BaseModel):
    """Absent fields are left unchanged; a supplied list replaces that collection."""

    # Decides what "last week" means when a question asks for an exact total.
    timezone: TimezoneName | None = None
    goals: list[GoalPayload] | None = Field(default=None, max_length=MAX_GOALS)
    constraints: list[ConstraintText] | None = Field(default=None, max_length=MAX_CONSTRAINTS)
    preferences: dict[PreferenceKey, PreferenceValue] | None = Field(
        default=None, max_length=MAX_PREFERENCES
    )
