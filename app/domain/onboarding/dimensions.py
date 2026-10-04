from enum import StrEnum


class ContextDimension(StrEnum):
    """The kinds of understanding onboarding sets out to build.

    Mirrors the confidence breakdown in the PRD: a profile is only useful when
    the system knows what the user wants, the situation they want it in, what
    limits them, and how they want to be worked with.
    """

    GOAL = "goal"
    CONTEXT = "context"
    CONSTRAINT = "constraint"
    PREFERENCE = "preference"


# Goals weigh most; preferences least since they're cheap to correct.
DIMENSION_WEIGHTS: dict[ContextDimension, float] = {
    ContextDimension.GOAL: 0.35,
    ContextDimension.CONTEXT: 0.25,
    ContextDimension.CONSTRAINT: 0.20,
    ContextDimension.PREFERENCE: 0.20,
}

# Pieces of evidence needed before a dimension counts as explored.
REQUIRED_EVIDENCE: dict[ContextDimension, int] = {
    ContextDimension.GOAL: 2,
    ContextDimension.CONTEXT: 2,
    ContextDimension.CONSTRAINT: 1,
    ContextDimension.PREFERENCE: 1,
}
