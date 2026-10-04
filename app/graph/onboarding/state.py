from typing import Protocol, TypedDict

from pydantic import BaseModel, Field

from app.domain.onboarding.context import CollectedContext
from app.domain.onboarding.dimensions import ContextDimension


class AskedQuestion(BaseModel):
    dimension: ContextDimension
    text: str


class ProfileDraft(BaseModel):
    """What the graph proposes writing to the user's profile.

    The graph produces a draft rather than persisting directly: persistence is a
    transaction the application layer owns, and keeping it out of the nodes is
    what lets the whole workflow be tested without a database.
    """

    goals: list[tuple[str, str]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    preferences: dict[str, str] = Field(default_factory=dict)


class OnboardingTurnState(TypedDict, total=False):
    """State for a single interview turn.

    One graph run advances the interview by one exchange: it folds in the answer
    just given and either produces the next question or declares the interview
    complete. State between turns lives in the database, so the workflow does not
    depend on a process staying alive across an HTTP round trip.
    """

    user_id: str

    # Both absent on the first run, which means "start the interview".
    pending_question: AskedQuestion | None
    answer: str | None

    asked_questions: list[AskedQuestion]
    questions_asked: int
    collected: CollectedContext

    coverage: dict[str, float]
    overall_confidence: float

    is_complete: bool
    completion_reason: str | None
    next_question: AskedQuestion | None
    profile_draft: ProfileDraft | None

    answer_was_usable: bool
    errors: list[str]


class OnboardingNode(Protocol):
    """What every node in the interview graph looks like.

    A node returns only the keys it changed, which is exactly an
    OnboardingTurnState because the TypedDict is total=False.

    Declared as a Protocol with a named ``state`` parameter rather than a bare
    Callable: LangGraph matches node callables structurally and rejects a
    positional-only signature.
    """

    async def __call__(self, state: OnboardingTurnState) -> OnboardingTurnState: ...
