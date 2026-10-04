import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.memory.types import MemoryType


class RetrievalSource(StrEnum):
    SEMANTIC = "semantic"
    KEYWORD = "keyword"


@dataclass(frozen=True)
class MemoryHit:
    """One memory found by one first-stage retriever.

    The score is that retriever's own, and is not comparable across retrievers:
    a cosine similarity and a full-text rank live on unrelated scales. Fusion
    works from positions for exactly that reason.
    """

    id: uuid.UUID
    content: str
    memory_type: MemoryType
    importance: float
    created_at: datetime
    score: float


@dataclass(frozen=True)
class RetrievedMemory:
    """A memory chosen for the model, after fusion and reranking."""

    id: uuid.UUID
    content: str
    memory_type: MemoryType
    importance: float
    relevance: float

    # Which retrievers found it, for traces and evaluation.
    sources: tuple[RetrievalSource, ...]
    similarity: float | None = None
