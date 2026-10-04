import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.temporal.ranges import TimeRange
from app.infrastructure.database.models import Event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActivityTotal:
    """An exact total for one activity over a period.

    Computed by the database. Nothing about this number is estimated, and the
    prompt that carries it says so, because a model asked to add up quantities
    from retrieved sentences produces something plausible rather than something
    correct.
    """

    activity: str
    occurrences: int
    total_quantity: float | None
    unit: str | None

    # Occurrences without an amount, reported rather than hidden.
    unquantified_occurrences: int = 0


@dataclass(frozen=True)
class ActivitySummary:
    time_range: TimeRange
    totals: tuple[ActivityTotal, ...]

    def is_empty(self) -> bool:
        return not self.totals


class StructuredActivityQuery:
    """Exact answers about what a user did, from the database rather than a model."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        # Own short session per search, so no connection is held during model calls.
        self._sessions = sessions

    async def summarise(
        self,
        user_id: uuid.UUID,
        time_range: TimeRange,
        *,
        activities: tuple[str, ...] = (),
        limit: int = 10,
    ) -> ActivitySummary:
        """Total each activity over a period.

        Grouped by unit as well as activity: adding hours to kilometres would
        produce a number with no meaning, so they are reported separately.
        """
        filters = [Event.user_id == user_id]
        if time_range.start is not None:
            filters.append(Event.occurred_at >= time_range.start)
        if time_range.end is not None:
            filters.append(Event.occurred_at < time_range.end)
        if activities:
            filters.append(Event.activity.in_([item.lower() for item in activities]))

        statement = (
            select(
                Event.activity,
                Event.unit,
                func.count().label("occurrences"),
                func.sum(Event.quantity).label("total_quantity"),
                func.count(Event.quantity).label("quantified"),
            )
            .where(*filters)
            .group_by(Event.activity, Event.unit)
            .order_by(func.count().desc(), Event.activity)
            .limit(limit)
        )

        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()

        totals = tuple(
            ActivityTotal(
                activity=activity,
                occurrences=int(occurrences),
                total_quantity=float(total_quantity) if total_quantity is not None else None,
                unit=unit,
                unquantified_occurrences=int(occurrences) - int(quantified),
            )
            for activity, unit, occurrences, total_quantity, quantified in rows
        )

        logger.info(
            "structured_activity_summarised",
            extra={
                "user_id": str(user_id),
                "range_spec": time_range.spec.value,
                "activity_groups": len(totals),
            },
        )

        return ActivitySummary(time_range=time_range, totals=totals)


def render_summary(summary: ActivitySummary) -> str:
    """Render exact totals for a prompt.

    Spelled out as counts and sums rather than prose so the model has nothing to
    recompute, and so a reader can see that the numbers came from somewhere.
    """
    lines: list[str] = []

    for total in summary.totals:
        parts = [f"{total.activity}: {total.occurrences} occurrence(s)"]
        if total.total_quantity is not None and total.unit:
            parts.append(f"totalling {_format_number(total.total_quantity)} {total.unit}")
        if total.unquantified_occurrences:
            parts.append(f"{total.unquantified_occurrences} without a recorded amount")
        lines.append(", ".join(parts))

    return "\n".join(lines)


def _format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"
