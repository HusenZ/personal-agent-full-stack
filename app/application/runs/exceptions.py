from app.core.exceptions import NotFoundError


class RunNotFoundError(NotFoundError):
    code = "RUN_NOT_FOUND"
    # Same error for missing and foreign runs, so existence isn't leaked.
    message = "Run was not found."
