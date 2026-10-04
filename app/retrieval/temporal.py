import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.temporal.ranges import TimeRange
from app.infrastructure.database.models import Event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimelineEntry:
    occurred_at: datetime
    activity: str
    description: str
    quantity: float | None = None
    unit: str | None = None


class TemporalEventRetriever:
    """Reads what happened over a period, in the order it happened."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        # Own short session per search, so no connection is held during model calls.
        self._sessions = sessions

    async def timeline(
        self,
        user_id: uuid.UUID,
        time_range: TimeRange,
        *,
        activities: tuple[str, ...] = (),
        limit: int = 25,
    ) -> list[TimelineEntry]:
        """The events in a period, oldest first.

        Ordered by when things happened rather than when they were recorded,
        because a question about change over time is a question about the former.
        Bounded, so a wide range cannot put a year of activity into a prompt.
        """
        filters = [Event.user_id == user_id]
        if time_range.start is not None:
            filters.append(Event.occurred_at >= time_range.start)
        if time_range.end is not None:
            filters.append(Event.occurred_at < time_range.end)
        if activities:
            filters.append(Event.activity.in_([item.lower() for item in activities]))

        # Newest first so the limit keeps recent events, then reversed for the prompt.
        statement = (
            select(Event)
            .where(*filters)
            .order_by(Event.occurred_at.desc(), Event.id.desc())
            .limit(limit)
        )
        async with self._sessions() as session:
            events = list((await session.execute(statement)).scalars().all())

        entries = [
            TimelineEntry(
                occurred_at=event.occurred_at,
                activity=event.activity,
                description=event.description,
                quantity=event.quantity,
                unit=event.unit,
            )
            for event in reversed(events)
        ]

        logger.info(
            "temporal_events_retrieved",
            extra={
                "user_id": str(user_id),
                "range_spec": time_range.spec.value,
                "event_count": len(entries),
            },
        )

        return entries


def render_timeline(entries: tuple[TimelineEntry, ...]) -> str:
    lines: list[str] = []

    for entry in entries:
        amount = (
            f" ({_format_number(entry.quantity)} {entry.unit})"
            if entry.quantity is not None and entry.unit
            else ""
        )
        day = entry.occurred_at.date().isoformat()
        lines.append(f"{day} - {entry.activity}: {entry.description}{amount}")

    return "\n".join(lines)


def _format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"
