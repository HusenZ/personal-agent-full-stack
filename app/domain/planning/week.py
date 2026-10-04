"""A week's plan: what the model proposes, and the rules it must pass.

The model only proposes. Dates, ordering, overlaps and citations are decided
here, deterministically, so a plan shown on a calendar never holds a block on a
day that does not exist, two blocks in the same slot, or a reason that points
at something the user never said.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field

DAYS_IN_PLAN = 7
MAX_BLOCKS_PER_DAY = 6
MIN_BLOCK_MINUTES = 5
MAX_BLOCK_MINUTES = 8 * 60

_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class BlockCategory(StrEnum):
    GOAL = "goal"
    HABIT = "habit"
    HEALTH = "health"
    LEARNING = "learning"
    ADMIN = "admin"
    SOCIAL = "social"
    REST = "rest"


class ProposedBlock(BaseModel):
    """One block of time as the model proposes it."""

    day: int = Field(description="0 is the first day of the plan, 6 the last.")
    start: str | None = Field(
        default=None,
        description="Local start time as HH:MM (24-hour), or null for any time that day.",
    )
    duration_minutes: int = Field(description="How long the block takes, in minutes.")
    title: str = Field(
        max_length=120, description="A checklist item of at most six words, e.g. 'Easy 5 km run'."
    )
    category: BlockCategory
    reason: str = Field(
        max_length=300,
        description="Why this block, in at most twelve words, tied to what the user said.",
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Ids of the evidence this block serves or respects, e.g. 'goal:1'.",
    )


class ProposedWeek(BaseModel):
    summary: str = Field(max_length=500, description="One short sentence on the week's shape.")
    blocks: list[ProposedBlock] = Field(default_factory=list, max_length=60)


@dataclass(frozen=True)
class PlanBlock:
    """A block that passed every rule, placed on a real date."""

    id: str
    on: date
    start: str | None
    duration_minutes: int
    title: str
    category: BlockCategory
    reason: str
    evidence_ids: tuple[str, ...]
    done: bool = False


@dataclass(frozen=True)
class CheckedWeek:
    blocks: tuple[PlanBlock, ...]
    rejected: dict[str, int] = field(default_factory=dict)


def plan_dates(first_day: date) -> tuple[date, ...]:
    return tuple(first_day + timedelta(days=offset) for offset in range(DAYS_IN_PLAN))


def check_week(
    proposal: ProposedWeek, first_day: date, evidence_ids: frozenset[str]
) -> CheckedWeek:
    """Keep the blocks that can be put on a calendar honestly.

    Rejections are counted by reason, so the counts can be logged and
    evaluated rather than silently absorbed.
    """
    rejected: dict[str, int] = {}

    def reject(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    dates = plan_dates(first_day)
    candidates: list[PlanBlock] = []
    for proposed in proposal.blocks:
        if not 0 <= proposed.day < DAYS_IN_PLAN:
            reject("day_out_of_range")
            continue
        if not MIN_BLOCK_MINUTES <= proposed.duration_minutes <= MAX_BLOCK_MINUTES:
            reject("bad_duration")
            continue
        start = proposed.start.strip() if proposed.start else None
        if start is not None and not _TIME.match(start):
            reject("bad_time")
            continue
        if start is not None and _minutes(start) + proposed.duration_minutes > 24 * 60:
            reject("past_midnight")
            continue
        title = proposed.title.strip()
        if not title:
            reject("empty_title")
            continue

        # Drop unknown citations, not the block itself.
        cited = tuple(dict.fromkeys(item for item in proposed.evidence_ids if item in evidence_ids))
        if len(cited) < len(set(proposed.evidence_ids)):
            reject("unknown_evidence")

        candidates.append(
            PlanBlock(
                id=str(uuid.uuid4()),
                on=dates[proposed.day],
                start=start,
                duration_minutes=proposed.duration_minutes,
                title=title,
                category=proposed.category,
                reason=proposed.reason.strip(),
                evidence_ids=cited,
            )
        )

    kept: list[PlanBlock] = []
    for day in dates:
        todays = sorted(
            (block for block in candidates if block.on == day),
            # Timed blocks in order, then the anytime ones in the order proposed.
            key=lambda block: (block.start is None, _minutes(block.start) if block.start else 0),
        )
        placed: list[PlanBlock] = []
        for block in todays:
            if len(placed) == MAX_BLOCKS_PER_DAY:
                reject("too_many_in_day")
                continue
            if block.start is not None and any(_overlaps(block, other) for other in placed):
                reject("overlap")
                continue
            placed.append(block)
        kept.extend(placed)

    return CheckedWeek(blocks=tuple(kept), rejected=rejected)


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def _overlaps(block: PlanBlock, other: PlanBlock) -> bool:
    if block.start is None or other.start is None:
        return False
    start, other_start = _minutes(block.start), _minutes(other.start)
    return (
        start < other_start + other.duration_minutes
        and other_start < start + block.duration_minutes
    )
