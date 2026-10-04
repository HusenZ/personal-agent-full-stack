from app.core.exceptions import ConflictError, NotFoundError


class AnalysisNotFoundError(NotFoundError):
    code = "ANALYSIS_NOT_FOUND"
    message = "No analysis has been generated yet."


class InsufficientContextError(ConflictError):
    code = "INSUFFICIENT_CONTEXT"
    message = (
        "There is not enough known about you to produce a grounded analysis. "
        "Complete onboarding or add goals to your profile first."
    )
