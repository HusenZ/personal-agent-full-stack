from app.core.exceptions import NotFoundError


class MemoryNotFoundError(NotFoundError):
    code = "MEMORY_NOT_FOUND"
    # Same error for missing and foreign memories, so existence isn't leaked.
    message = "Memory was not found."
