from datetime import UTC, datetime

from starlette import status

from app.api.errors import resolve_status_code
from app.application.subscription.exceptions import QuotaExceededError
from app.core.exceptions import (
    ApplicationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.domain.subscription.plans import Feature, Plan


class UserNotFoundError(NotFoundError):
    code = "USER_NOT_FOUND"
    message = "User was not found."


def test_known_errors_map_to_expected_status_codes():
    assert resolve_status_code(NotFoundError()) == status.HTTP_404_NOT_FOUND
    assert resolve_status_code(PermissionDeniedError()) == status.HTTP_403_FORBIDDEN
    assert resolve_status_code(ConflictError()) == status.HTTP_409_CONFLICT


def test_unmapped_application_error_defaults_to_server_error():
    assert resolve_status_code(ApplicationError()) == status.HTTP_500_INTERNAL_SERVER_ERROR


def test_subclass_inherits_mapping_of_its_parent():
    assert resolve_status_code(UserNotFoundError()) == status.HTTP_404_NOT_FOUND


def test_custom_message_overrides_class_default():
    error = NotFoundError("Memory 42 was not found.")

    assert error.message == "Memory 42 was not found."
    assert error.code == "NOT_FOUND"


def test_default_message_is_used_when_none_given():
    assert NotFoundError().message == NotFoundError.message


def test_an_exhausted_allowance_asks_for_payment_rather_than_a_retry():
    error = QuotaExceededError(
        Plan.FREE, Feature.CHAT_MESSAGE, 50, datetime(2026, 11, 1, tzinfo=UTC)
    )

    assert resolve_status_code(error) == status.HTTP_402_PAYMENT_REQUIRED
