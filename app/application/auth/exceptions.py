from app.core.exceptions import (
    ApplicationError,
    AuthenticationError,
    ConflictError,
    PermissionDeniedError,
)


class EmailAlreadyRegisteredError(ConflictError):
    code = "EMAIL_ALREADY_REGISTERED"
    message = "An account with this email already exists."


class InvalidCredentialsError(AuthenticationError):
    code = "INVALID_CREDENTIALS"
    # Same error for unknown email and wrong password to prevent enumeration.
    message = "Email or password is incorrect."


class InvalidRefreshTokenError(AuthenticationError):
    code = "INVALID_REFRESH_TOKEN"
    message = "Refresh token is invalid, expired or already used."


class InactiveUserError(AuthenticationError):
    code = "INACTIVE_USER"
    message = "This account is not active."


class IncorrectPasswordError(PermissionDeniedError):
    # 403, not 401: the session is valid, only the password check failed.
    code = "INCORRECT_PASSWORD"
    message = "The password is incorrect."


__all__ = [
    "ApplicationError",
    "EmailAlreadyRegisteredError",
    "InactiveUserError",
    "IncorrectPasswordError",
    "InvalidCredentialsError",
    "InvalidRefreshTokenError",
]
