from dataclasses import dataclass

from app.domain.memory.types import MemoryDurability, MemoryType


@dataclass(frozen=True)
class MemoryPolicy:
    """What is worth remembering, and what is merely worth having heard.

    The product's thesis is that a personal assistant is useful because it
    remembers selectively. These thresholds are where "selectively" is defined.
    """

    # Below this, a memory isn't worth its place in a prompt.
    min_importance: float = 0.35

    # A guess about the user is worse than no memory of them at all.
    min_confidence: float = 0.5

    # Cap comparisons so extraction stays bounded for users with many memories.
    max_comparisons: int = 25

    # How many pairs a model is asked to judge in one pass.
    max_judged_pairs: int = 10

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_importance <= 1.0:
            raise ValueError("min_importance must be within [0, 1]")
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be within [0, 1]")
        if self.max_comparisons < 1:
            raise ValueError("max_comparisons must be at least 1")
        if self.max_judged_pairs < 1:
            raise ValueError("max_judged_pairs must be at least 1")


DEFAULT_MEMORY_POLICY = MemoryPolicy()

# How long each durability stays relevant.
DURABILITY_LIFETIME_DAYS: dict[MemoryDurability, int | None] = {
    MemoryDurability.LONG_TERM: None,
    MemoryDurability.SHORT_TERM: 90,
    MemoryDurability.TEMPORARY: 1,
}

# Types whose durability is fixed, not chosen by the model.
DURABILITY_BY_TYPE: dict[MemoryType, MemoryDurability] = {
    MemoryType.GOAL: MemoryDurability.LONG_TERM,
    MemoryType.PREFERENCE: MemoryDurability.LONG_TERM,
    MemoryType.CONSTRAINT: MemoryDurability.LONG_TERM,
    MemoryType.TEMPORARY_CONTEXT: MemoryDurability.TEMPORARY,
}
