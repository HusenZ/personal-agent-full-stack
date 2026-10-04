class ApplicationError(Exception):
    """Base class for errors that carry a client-safe code and message.

    Subclasses describe *what went wrong* in domain terms. The HTTP status code
    is deliberately not defined here so that the domain and application layers
    remain free of transport concerns; the API layer owns that mapping.
    """

    code: str = "INTERNAL_ERROR"
    message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or type(self).message
        super().__init__(self.message)


class AuthenticationError(ApplicationError):
    code = "UNAUTHENTICATED"
    message = "Authentication is required."


class NotFoundError(ApplicationError):
    code = "NOT_FOUND"
    message = "The requested resource was not found."


class ConflictError(ApplicationError):
    code = "CONFLICT"
    message = "The request conflicts with the current state of the resource."


class RateLimitedError(ApplicationError):
    """Raised when a caller has exceeded a rate limit.

    Carries the wait so the API layer can answer with Retry-After; a 429 without
    it leaves a well-behaved client no option but to guess.
    """

    code = "RATE_LIMITED"
    message = "Too many requests. Please try again later."

    def __init__(self, retry_after_seconds: int, message: str | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class PermissionDeniedError(ApplicationError):
    code = "PERMISSION_DENIED"
    message = "You do not have access to this resource."


class ServiceUnavailableError(ApplicationError):
    """A dependency failed in a way that retrying later could fix."""

    code = "SERVICE_UNAVAILABLE"
    message = "The service is temporarily unavailable. Please try again."
