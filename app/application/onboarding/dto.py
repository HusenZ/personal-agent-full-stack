import uuid
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.domain.onboarding.context import CollectedContext
from app.graph.onboarding.state import AskedQuestion


class OnboardingSessionState(BaseModel):
    """The interview state persisted between HTTP turns.

    Validated on the way back out of JSONB rather than trusted: a document
    written by an older revision of the graph must fail loudly here instead of
    silently producing a half-populated interview.
    """

    collected: CollectedContext = Field(default_factory=CollectedContext)
    asked_questions: list[AskedQuestion] = Field(default_factory=list)
    pending_question: AskedQuestion | None = None
    coverage: dict[str, float] = Field(default_factory=dict)
    completion_reason: str | None = None


@dataclass(frozen=True)
class OnboardingTurn:
    session_id: uuid.UUID
    status: str
    questions_asked: int
    overall_confidence: float
    coverage: dict[str, float] = field(default_factory=dict)
    question: AskedQuestion | None = None
    is_complete: bool = False
