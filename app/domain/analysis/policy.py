from dataclasses import dataclass


@dataclass(frozen=True)
class AnalysisPolicy:
    """Product policy for how hard to try, and what counts as good enough."""

    # One retry with feedback fixes most failures; more mostly wastes money.
    max_generation_attempts: int = 2

    # Fewer surviving claims than this is a thin analysis, worth one more attempt.
    min_claims: int = 3

    def __post_init__(self) -> None:
        if self.max_generation_attempts < 1:
            raise ValueError("max_generation_attempts must be at least 1")
        if self.min_claims < 1:
            raise ValueError("min_claims must be at least 1")


DEFAULT_ANALYSIS_POLICY = AnalysisPolicy()
