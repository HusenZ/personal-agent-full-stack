from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Literal
from zoneinfo import ZoneInfo

from app.infrastructure.database.models import Event


class Unset(Enum):
    """Marks a field an update did not mention.

    Needed because some event fields can be cleared: ``quantity=None`` means
    "this occurrence had no amount", which is a different request from leaving
    the amount as it is.
    """

    MARKER = "unset"


UNSET = Unset.MARKER

type Maybe[ValueT] = ValueT | Literal[Unset.MARKER]


@dataclass(frozen=True)
class EventFilters:
    # Normalised activity labels; any of them matches. Empty means every activity.
    activities: tuple[str, ...] = ()

    # Local calendar days in the user's time zone, both inclusive.
    start_date: date | None = None
    end_date: date | None = None


@dataclass(frozen=True)
class EventPage:
    items: list[Event] = field(default_factory=list)
    total_items: int = 0

    # The zone dates were read and filtered in, so responses report days the same way.
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("UTC"))


@dataclass(frozen=True)
class EventResult:
    event: Event
    timezone: ZoneInfo


@dataclass(frozen=True)
class UpdateEventCommand:
    """A partial update. UNSET fields are left unchanged.

    ``activity``, ``description`` and ``on`` can only be replaced. ``quantity``
    and ``unit`` can also be cleared with None.
    """

    activity: Maybe[str] = UNSET
    description: Maybe[str] = UNSET

    # The local calendar day, in the user's time zone.
    on: Maybe[date] = UNSET

    quantity: Maybe[float | None] = UNSET
    unit: Maybe[str | None] = UNSET

    def changed_fields(self) -> tuple[str, ...]:
        """Which fields the update names, for logs that must not carry values."""
        values = {
            "activity": self.activity,
            "description": self.description,
            "date": self.on,
            "quantity": self.quantity,
            "unit": self.unit,
        }
        return tuple(name for name, value in values.items() if value is not UNSET)
