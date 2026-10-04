"""The evaluation dataset: synthetic users, and questions labelled against them.

Kept as version-controlled JSON so that a change to a label is reviewed like a
change to code, and so a result can always be traced to the exact cases it was
measured on.
"""

import json
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.domain.memory.types import MemoryType
from app.domain.retrieval.plan import RetrievalNeed

DATASETS_DIRECTORY = Path(__file__).resolve().parents[2] / "evaluation" / "datasets"


class CaseCategory(StrEnum):
    # Shares its key words with the memory that answers it.
    DIRECT = "direct"
    # Says the same thing in different words; only meaning connects them.
    PARAPHRASE = "paraphrase"
    # Turns on a name or rare term an embedding tends to blur.
    NAMED = "named"
    # Needs several memories together.
    MULTI = "multi"
    # Needs exact figures or a timeline rather than recalled sentences.
    TEMPORAL = "temporal"
    # Nothing the user has said answers it; the right answer admits that.
    UNANSWERABLE = "unanswerable"


class SeedMemory(BaseModel):
    key: str = Field(min_length=1, max_length=50)
    content: str = Field(min_length=1, max_length=1000)
    memory_type: MemoryType
    importance: float = Field(default=0.6, ge=0, le=1)
    # How long ago it was said, which is what recency in reranking reads.
    days_ago: int = Field(default=10, ge=0, le=3650)


class SeedEvent(BaseModel):
    activity: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    days_ago: int = Field(ge=0, le=3650)
    quantity: float | None = None
    unit: str | None = None


class Persona(BaseModel):
    key: str = Field(min_length=1, max_length=50)
    timezone: str = "UTC"
    goals: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    preferences: dict[str, str] = Field(default_factory=dict)
    memories: list[SeedMemory] = Field(default_factory=list)
    events: list[SeedEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_memory_keys(self) -> Self:
        keys = [memory.key for memory in self.memories]
        if len(keys) != len(set(keys)):
            raise ValueError(f"persona {self.key} repeats a memory key")
        return self


class EvaluationCase(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    persona: str
    question: str = Field(min_length=1, max_length=1000)
    category: CaseCategory

    # Memory key to graded relevance: 2 answers the question, 1 helps.
    relevant: dict[str, int] = Field(default_factory=dict)

    # Checked as a subset; extra needs cost latency, not correctness.
    expected_needs: list[RetrievalNeed] = Field(default_factory=list)

    # Exact figures a correct answer states, verified against SQL in tests.
    expected_figures: list[float] = Field(default_factory=list)

    notes: str = ""

    @model_validator(mode="after")
    def _labels_are_coherent(self) -> Self:
        if any(grade not in (1, 2) for grade in self.relevant.values()):
            raise ValueError(f"case {self.id}: relevance grades are 1 or 2")
        if self.category is CaseCategory.UNANSWERABLE and self.relevant:
            raise ValueError(f"case {self.id}: an unanswerable case has no relevant memories")
        if self.category is not CaseCategory.UNANSWERABLE and not (
            self.relevant or self.expected_figures
        ):
            raise ValueError(f"case {self.id}: label what answers it")
        return self


class Dataset(BaseModel):
    name: str
    version: str
    # Whether a person has reviewed every label.
    reviewed: bool = False
    personas: list[Persona]
    cases: list[EvaluationCase]

    @model_validator(mode="after")
    def _references_resolve(self) -> Self:
        personas = {persona.key: persona for persona in self.personas}
        if len(personas) != len(self.personas):
            raise ValueError("persona keys must be unique")

        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")

        for case in self.cases:
            persona = personas.get(case.persona)
            if persona is None:
                raise ValueError(f"case {case.id}: unknown persona {case.persona}")
            known = {memory.key for memory in persona.memories}
            unknown = set(case.relevant) - known
            if unknown:
                raise ValueError(f"case {case.id}: unknown memories {sorted(unknown)}")
        return self

    def persona(self, key: str) -> Persona:
        return next(persona for persona in self.personas if persona.key == key)


def load_dataset(name: str, directory: Path = DATASETS_DIRECTORY) -> Dataset:
    path = directory / f"{name}.json"
    return Dataset.model_validate(json.loads(path.read_text()))
