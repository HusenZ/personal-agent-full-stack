import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.domain.onboarding.dimensions import ContextDimension

# Matches the prompt's untrusted-content budget; reject rather than truncate.
AnswerText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class SubmitAnswerRequest(BaseModel):
    answer: AnswerText


class OnboardingQuestionResponse(BaseModel):
    text: str
    dimension: ContextDimension


class OnboardingTurnResponse(BaseModel):
    session_id: uuid.UUID
    status: Literal["in_progress", "completed", "abandoned"]
    is_complete: bool
    questions_asked: int
    overall_confidence: float
    coverage: dict[str, float] = Field(default_factory=dict)
    question: OnboardingQuestionResponse | None = None
