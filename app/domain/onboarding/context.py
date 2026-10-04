from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.domain.onboarding.dimensions import ContextDimension

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
KeyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

GoalPriorityLiteral = Literal["low", "medium", "high"]

# Caps per extraction; a huge list means the model misread the task.
MAX_ITEMS_PER_KIND = 10


class ExtractedGoal(BaseModel):
    name: ShortText
    priority: GoalPriorityLiteral = "medium"
    confidence: Confidence


class ExtractedConstraint(BaseModel):
    description: LongText
    confidence: Confidence


class ExtractedPreference(BaseModel):
    key: KeyText
    value: LongText
    confidence: Confidence


class ExtractedObservation(BaseModel):
    """Something true about the user's situation that is not a goal or a limit."""

    content: LongText
    confidence: Confidence


class ExtractionResult(BaseModel):
    """What the model is allowed to return from one answer.

    Doubles as the structured-output schema handed to the provider, so the shape
    the model is constrained to and the shape the domain reasons about cannot
    drift apart.
    """

    answered: bool = Field(
        default=True,
        description="False when the user declined, skipped, or said nothing usable.",
    )
    goals: list[ExtractedGoal] = Field(default_factory=list, max_length=MAX_ITEMS_PER_KIND)
    constraints: list[ExtractedConstraint] = Field(
        default_factory=list, max_length=MAX_ITEMS_PER_KIND
    )
    preferences: list[ExtractedPreference] = Field(
        default_factory=list, max_length=MAX_ITEMS_PER_KIND
    )
    observations: list[ExtractedObservation] = Field(
        default_factory=list, max_length=MAX_ITEMS_PER_KIND
    )

    def is_empty(self) -> bool:
        return not (self.goals or self.constraints or self.preferences or self.observations)


class CollectedContext(BaseModel):
    """Everything onboarding has learned so far, accumulated across turns."""

    goals: list[ExtractedGoal] = Field(default_factory=list)
    constraints: list[ExtractedConstraint] = Field(default_factory=list)
    preferences: list[ExtractedPreference] = Field(default_factory=list)
    observations: list[ExtractedObservation] = Field(default_factory=list)

    def confidences_for(self, dimension: ContextDimension) -> list[float]:
        match dimension:
            case ContextDimension.GOAL:
                return [item.confidence for item in self.goals]
            case ContextDimension.CONSTRAINT:
                return [item.confidence for item in self.constraints]
            case ContextDimension.PREFERENCE:
                return [item.confidence for item in self.preferences]
            case ContextDimension.CONTEXT:
                return [item.confidence for item in self.observations]


def merge_extraction(context: CollectedContext, extraction: ExtractionResult) -> CollectedContext:
    """Fold a turn's extraction into the accumulated context.

    Returns a new value rather than mutating, so a failed turn cannot leave the
    session holding half-applied state.

    Duplicates are resolved by keeping the higher-confidence entry: re-asking
    about a goal should sharpen what is known about it, not record it twice.
    """
    return CollectedContext(
        goals=_merge_by_key(context.goals, extraction.goals, key=lambda goal: goal.name.lower()),
        constraints=_merge_by_key(
            context.constraints,
            extraction.constraints,
            key=lambda constraint: constraint.description.lower(),
        ),
        preferences=_merge_by_key(
            context.preferences,
            extraction.preferences,
            key=lambda preference: preference.key.lower(),
        ),
        observations=_merge_by_key(
            context.observations,
            extraction.observations,
            key=lambda observation: observation.content.lower(),
        ),
    )


class _Confident(BaseModel):
    confidence: Confidence


def _merge_by_key[ItemT: _Confident](
    existing: list[ItemT],
    incoming: list[ItemT],
    key: Callable[[ItemT], str],
) -> list[ItemT]:
    merged: dict[str, ItemT] = {key(item): item for item in existing}

    for item in incoming:
        item_key = key(item)
        current = merged.get(item_key)
        if current is None or item.confidence > current.confidence:
            merged[item_key] = item

    return list(merged.values())
