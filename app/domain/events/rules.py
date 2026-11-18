"""What makes an event record coherent.

An event is summed by the database, so the rules here are about keeping totals
meaningful rather than about presentation: an amount needs a unit to be added
up with anything, and a date in the future is not something the user did.
"""

from datetime import date

# The earliest day an event may be placed on or filtered from. Far enough back
# for anything a person could log about their own life, and it keeps local-day
# arithmetic well away from the calendar's limits, where converting to UTC
# would overflow.
EARLIEST_EVENT_DATE = date(1900, 1, 1)

# The latest day a date filter may name. Filters may look ahead, though no
# event is ever placed in the future; the bound only keeps "the day after" in
# range. Omit the filter for an open end.
LATEST_FILTER_DATE = date(2999, 12, 31)

# Generous for a single occurrence of anything personal, and small enough that
# summing many of them stays exact in a double and readable when rendered.
MAX_EVENT_QUANTITY = 1_000_000.0

# Caps a list filter so a query string cannot produce an unbounded IN list.
MAX_ACTIVITY_FILTERS = 20


def amount_is_coherent(quantity: float | None, unit: str | None) -> bool:
    """Whether an amount and its unit are both present or both absent.

    Totals are grouped by unit and only rendered with one, so a quantity without
    a unit is summed but never shown, and a unit without a quantity describes
    nothing. Extraction can still produce either; an edit must not.
    """
    return (quantity is None) == (unit is None)


def is_in_future(day: date, today: date) -> bool:
    """Whether a day is after the user's own today.

    ``today`` is the user's, not the server's: for someone in UTC+13 it is
    already tomorrow in London, and their today must be accepted.
    """
    return day > today
