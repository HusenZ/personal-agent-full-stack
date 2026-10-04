from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.domain.temporal.ranges import TimeRangeSpec

ActivityLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=100)
]


class RetrievalNeed(StrEnum):
    """What a question needs in order to be answered well.

    Not mutually exclusive: "how much did I study last week, and why do I keep
    slipping?" needs an exact total and the surrounding context both.
    """

    SEMANTIC = "semantic"
    TIMELINE = "timeline"
    TOTALS = "totals"


class QueryPlan(BaseModel):
    """How to gather what a question needs.

    The model decides *what kind* of information would help and *which period*
    the question is about. It does not compute dates, and it does not compute
    totals: naming a period is a language task, and resolving one is arithmetic
    that models get wrong quietly.
    """

    needs: list[RetrievalNeed] = Field(
        default_factory=lambda: [RetrievalNeed.SEMANTIC],
        max_length=3,
        description="Every kind of information this question needs.",
    )
    time_range: TimeRangeSpec = Field(
        default=TimeRangeSpec.ALL_TIME,
        description="The period the question is about, if it is about one.",
    )
    activities: list[ActivityLabel] = Field(
        default_factory=list,
        max_length=5,
        description="Activities the question is about, as single lowercase words.",
    )

    def needs_semantic(self) -> bool:
        return RetrievalNeed.SEMANTIC in self.needs or not self.needs

    def needs_timeline(self) -> bool:
        return RetrievalNeed.TIMELINE in self.needs

    def needs_totals(self) -> bool:
        return RetrievalNeed.TOTALS in self.needs
