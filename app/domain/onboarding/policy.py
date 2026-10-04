from dataclasses import dataclass


@dataclass(frozen=True)
class OnboardingPolicy:
    """Product policy for when onboarding has learned enough.

    Kept as a value object rather than environment configuration because these
    are product decisions, not deployment ones, and evaluation runs need to sweep
    them without mutating the environment.
    """

    confidence_threshold: float = 0.75

    # Always ask a minimum number of questions.
    min_questions: int = 4

    # Hard stop so evasive answers can't extend the interview forever.
    max_questions: int = 12

    def __post_init__(self) -> None:
        if not 0.0 < self.confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be within (0, 1]")
        if self.min_questions < 1:
            raise ValueError("min_questions must be at least 1")
        if self.max_questions < self.min_questions:
            raise ValueError("max_questions must be at least min_questions")


DEFAULT_ONBOARDING_POLICY = OnboardingPolicy()
