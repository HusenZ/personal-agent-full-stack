import datetime as dt
import re
import uuid
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.domain.events.rules import (
    EARLIEST_EVENT_DATE,
    LATEST_FILTER_DATE,
    MAX_EVENT_QUANTITY,
)
from app.schemas.common import reject_nul_characters

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def require_iso_date(value: Any) -> Any:
    """Accept a calendar date only as YYYY-MM-DD.

    Pydantic would otherwise also read "2026-11-01T00:00:00" and a midnight
    Unix timestamp as dates. Either suggests a client that thinks it is sending
    an instant, and quietly dropping the time would hide that a time zone was
    never applied.
    """
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        raise ValueError("must be a calendar date in YYYY-MM-DD format")
    return value


def require_json_number(value: Any) -> Any:
    """Accept an amount only as a JSON number.

    Lax parsing would turn true into 1.0 and "2" into 2.0; a total built on a
    misread amount looks exact and is not.
    """
    if value is None or (isinstance(value, int | float) and not isinstance(value, bool)):
        return value
    raise ValueError("must be a number")


# Normalised as extraction normalises them, so edited and extracted events aggregate together.
EventActivity = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=100),
    AfterValidator(reject_nul_characters),
]
EventUnit = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=32),
    AfterValidator(reject_nul_characters),
]
EventDescription = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
    AfterValidator(reject_nul_characters),
]
EventQuantity = Annotated[
    float,
    BeforeValidator(require_json_number),
    Field(ge=0, le=MAX_EVENT_QUANTITY, allow_inf_nan=False),
]
# The day an event happened. Never in the future, which needs the user's zone
# to decide and is checked when the update is applied.
EventDate = Annotated[dt.date, BeforeValidator(require_iso_date), Field(ge=EARLIEST_EVENT_DATE)]
# A day a list filter starts or ends on. May be in the future.
FilterDate = Annotated[
    dt.date,
    BeforeValidator(require_iso_date),
    Field(ge=EARLIEST_EVENT_DATE, le=LATEST_FILTER_DATE),
]


class EventResponse(BaseModel):
    id: uuid.UUID
    activity: str
    description: str

    # Null when no amount was recorded; only events with both are summed.
    quantity: float | None = None
    unit: str | None = None

    date: dt.date = Field(
        description="The day it happened, in `timezone`. What the date filters match on."
    )
    occurred_at: dt.datetime = Field(
        description=(
            "The stored instant. Only the day is known, so events are placed at "
            "midday on it in the user's time zone."
        )
    )
    timezone: str = Field(
        description=(
            "The time zone `date` is given in: the profile's, or UTC when the "
            "profile's is missing or not recognised."
        )
    )

    # The memory it was recorded alongside, if any. Neither changes the other.
    memory_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None

    created_at: dt.datetime
    updated_at: dt.datetime


# Fields that may be omitted but, when present, must hold a value.
_REPLACE_ONLY = ("activity", "description", "date")


class UpdateEventRequest(BaseModel):
    """A partial update. Absent fields are left unchanged.

    ``quantity`` and ``unit`` can be cleared with null. The other fields can
    only be replaced: an event always has an activity, a description and a day.
    """

    activity: EventActivity | None = None
    description: EventDescription | None = None
    date: EventDate | None = Field(
        default=None, description="The local day it happened, YYYY-MM-DD, in your time zone."
    )
    quantity: EventQuantity | None = None
    unit: EventUnit | None = None

    @field_validator(*_REPLACE_ONLY, mode="before")
    @classmethod
    def _reject_null(cls, value: Any) -> Any:
        # Runs only for fields actually sent, so omitting one is still fine.
        if value is None:
            raise ValueError("cannot be null; omit the field to leave it unchanged")
        return value

    @model_validator(mode="after")
    def _reject_empty_update(self) -> "UpdateEventRequest":
        # Unknown fields are ignored rather than counted, as for memories.
        if not self.model_fields_set:
            raise ValueError("at least one field must be supplied")
        return self
