from app.core.exceptions import InvalidRequestError, NotFoundError


class EventNotFoundError(NotFoundError):
    code = "EVENT_NOT_FOUND"
    # Same error for missing and foreign events, so existence isn't leaked.
    message = "Event was not found."


class EventAmountIncompleteError(InvalidRequestError):
    code = "EVENT_AMOUNT_INCOMPLETE"
    message = (
        "An amount and its unit must be set together or cleared together; "
        "the update would leave one without the other."
    )


class EventDateInFutureError(InvalidRequestError):
    code = "EVENT_DATE_IN_FUTURE"
    message = "An event's date cannot be after today in your time zone."


class InvalidDateRangeError(InvalidRequestError):
    code = "INVALID_DATE_RANGE"
    message = "end_date must be on or after start_date."
