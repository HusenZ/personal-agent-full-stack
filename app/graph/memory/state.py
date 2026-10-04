from typing import Protocol, TypedDict

from app.domain.memory.decisions import MemoryDecision
from app.domain.memory.pairing import ExistingMemory
from app.domain.memory.types import MemoryCandidate


class MemoryExtractionState(TypedDict, total=False):
    """State for deciding what one exchange is worth remembering."""

    user_id: str
    conversation_id: str

    user_message: str
    assistant_reply: str

    # Passed in so the pipeline runs without a database.
    existing: list[ExistingMemory]

    candidates: list[MemoryCandidate]
    admitted: list[MemoryCandidate]

    # Aligned with admitted; None where embedding failed.
    embeddings: list[tuple[float, ...] | None]
    decisions: list[MemoryDecision]

    # How many candidates the model proposed, worth watching.
    rejected_count: int
    judged_pairs: int

    errors: list[str]


class MemoryNode(Protocol):
    """What every node in the memory graph looks like.

    Declared as a Protocol with a named ``state`` parameter rather than a bare
    Callable: LangGraph matches node callables structurally and rejects a
    positional-only signature.
    """

    async def __call__(self, state: MemoryExtractionState) -> MemoryExtractionState: ...
