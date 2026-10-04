import uuid
from dataclasses import dataclass

from pydantic import BaseModel, Field, field_validator

from app.domain.conversation.context import RecalledMemory

MAX_SEARCH_QUERY_LENGTH = 200


class EvidenceGrade(BaseModel):
    """A model's judgement of whether recalled memories can answer a question.

    Grading and rewriting are one call rather than two: working out what is
    missing is most of the work of searching for it, and a second call per
    retry would add a model round trip to the slowest path a turn can take.

    The grader names the memories to drop rather than the ones to keep, so an
    omission keeps a memory instead of silently losing it. Keeping one too many
    is the behaviour before grading existed; dropping a relevant one is a new
    way to answer worse.
    """

    irrelevant: list[int] = Field(
        default_factory=list,
        max_length=50,
        description="Numbers of the memories that do not bear on the question.",
    )
    sufficient: bool = Field(
        description=(
            "True when the relevant memories, together with any other information "
            "listed, are enough to answer, or when the message needs nothing "
            "personal at all."
        ),
    )
    search_query: str = Field(
        default="",
        max_length=MAX_SEARCH_QUERY_LENGTH,
        description=(
            "Only when not sufficient: a short search in plain words for what is "
            "missing, different from every search already tried."
        ),
    )

    @field_validator("search_query")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


@dataclass(frozen=True)
class EvidencePolicy:
    """How hard to look for better evidence before answering with what there is."""

    enabled: bool = True

    # Includes the first retrieval, so 2 allows one rewrite.
    max_retrieval_attempts: int = 2

    # Cap what the grader sees across attempts.
    max_graded_memories: int = 12

    def __post_init__(self) -> None:
        if self.max_retrieval_attempts < 1:
            raise ValueError("max_retrieval_attempts must be at least 1")
        if self.max_graded_memories < 1:
            raise ValueError("max_graded_memories must be at least 1")


DEFAULT_EVIDENCE_POLICY = EvidencePolicy()


def apply_grade(memories: list[RecalledMemory], grade: EvidenceGrade) -> list[RecalledMemory]:
    """Drop the memories the grader judged irrelevant.

    Numbers are one-based, matching how the memories were shown. A number that
    matches nothing is ignored rather than trusted to mean something.
    """
    dropped = set(grade.irrelevant)
    return [memory for number, memory in enumerate(memories, start=1) if number not in dropped]


def merge_memories(
    kept: list[RecalledMemory], found: list[RecalledMemory], *, limit: int
) -> list[RecalledMemory]:
    """Combine what survived grading with what a new search found.

    A memory found twice keeps its higher relevance: each search scores against
    the best achievable result, so the two scores are on the same scale.
    """
    by_id: dict[uuid.UUID, RecalledMemory] = {}
    for memory in [*kept, *found]:
        existing = by_id.get(memory.id)
        if existing is None or memory.relevance > existing.relevance:
            by_id[memory.id] = memory

    # Id as the final key so equal relevance orders the same way on every run.
    ordered = sorted(by_id.values(), key=lambda memory: (-memory.relevance, str(memory.id)))
    return ordered[:limit]


def next_search_query(grade: EvidenceGrade | None, tried: list[str]) -> str | None:
    """The search worth running next, or None when there is none.

    A search already tried returns the same memories again, since retrieval is
    deterministic for a given query. Repeating one would spend an attempt
    learning nothing, so it ends the loop rather than continuing it.
    """
    if grade is None or grade.sufficient:
        return None

    query = " ".join(grade.search_query.split())
    if not query:
        return None

    if query.casefold() in {" ".join(previous.split()).casefold() for previous in tried}:
        return None

    return query
