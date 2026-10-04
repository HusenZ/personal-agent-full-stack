from app.core.exceptions import ConflictError, NotFoundError


class OnboardingNotStartedError(NotFoundError):
    code = "ONBOARDING_NOT_STARTED"
    message = "Onboarding has not been started."


class OnboardingAlreadyCompletedError(ConflictError):
    code = "ONBOARDING_ALREADY_COMPLETED"
    message = "Onboarding has already been completed."
