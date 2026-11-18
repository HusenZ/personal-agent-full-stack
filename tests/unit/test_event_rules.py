import uuid
from datetime import date

import pytest
from pydantic import ValidationError
from starlette import status

from app.api.errors import resolve_status_code
from app.api.v1.events import to_update_command
from app.application.events.dto import UNSET
from app.application.events.exceptions import (
    EventAmountIncompleteError,
    EventDateInFutureError,
    EventNotFoundError,
    InvalidDateRangeError,
)
from app.core.exceptions import ApplicationError
from app.domain.events.rules import amount_is_coherent, is_in_future
from app.schemas.event import UpdateEventRequest


class TestAmountCoherence:
    @pytest.mark.parametrize(
        ("quantity", "unit", "coherent"),
        [
            (2.0, "hours", True),
            (None, None, True),
            # Zero is an amount: "ran 0 km" is not "did not say how far".
            (0.0, "km", True),
            (2.0, None, False),
            (None, "hours", False),
        ],
    )
    def test_an_amount_and_its_unit_come_together(
        self, quantity: float | None, unit: str | None, coherent: bool
    ):
        assert amount_is_coherent(quantity, unit) is coherent


class TestFutureDates:
    def test_today_is_not_the_future(self):
        assert not is_in_future(date(2026, 11, 17), today=date(2026, 11, 17))

    def test_tomorrow_is(self):
        assert is_in_future(date(2026, 11, 18), today=date(2026, 11, 17))


class TestErrorMapping:
    def test_a_missing_event_is_not_found(self):
        assert resolve_status_code(EventNotFoundError()) == status.HTTP_404_NOT_FOUND
        assert EventNotFoundError().code == "EVENT_NOT_FOUND"

    @pytest.mark.parametrize(
        "error",
        [
            EventAmountIncompleteError(field="body.unit"),
            EventDateInFutureError(field="body.date"),
            InvalidDateRangeError(field="query.end_date"),
        ],
    )
    def test_a_rule_the_input_breaks_is_unprocessable(self, error: ApplicationError):
        assert resolve_status_code(error) == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestUpdateRequest:
    def test_absent_fields_stay_unset_so_nothing_else_changes(self):
        command = to_update_command(UpdateEventRequest.model_validate({"quantity": 3}))

        assert command.quantity == 3
        assert (command.activity, command.description, command.on, command.unit) == (
            UNSET,
            UNSET,
            UNSET,
            UNSET,
        )
        assert command.changed_fields() == ("quantity",)

    def test_an_explicit_null_clears_an_amount_rather_than_leaving_it(self):
        command = to_update_command(
            UpdateEventRequest.model_validate({"quantity": None, "unit": None})
        )

        assert (command.quantity, command.unit) == (None, None)
        assert command.changed_fields() == ("quantity", "unit")

    @pytest.mark.parametrize("field", ["activity", "description", "date"])
    def test_fields_every_event_has_cannot_be_nulled(self, field: str):
        with pytest.raises(ValidationError) as caught:
            UpdateEventRequest.model_validate({field: None})

        assert caught.value.errors()[0]["loc"] == (field,)

    def test_the_date_is_a_calendar_day(self):
        command = to_update_command(UpdateEventRequest.model_validate({"date": "2026-01-05"}))

        assert command.on == date(2026, 1, 5)
        assert command.changed_fields() == ("date",)

    def test_unknown_fields_alone_are_an_empty_update(self):
        with pytest.raises(ValidationError, match="at least one field"):
            UpdateEventRequest.model_validate({"id": str(uuid.uuid4())})
