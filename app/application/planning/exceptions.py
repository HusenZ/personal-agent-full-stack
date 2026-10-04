from app.core.exceptions import ConflictError, NotFoundError, ServiceUnavailableError


class PlanNotFoundError(NotFoundError):
    code = "PLAN_NOT_FOUND"
    # Identical whether the plan does not exist or belongs to someone else.
    message = "No plan was found."


class PlanBlockNotFoundError(NotFoundError):
    code = "PLAN_BLOCK_NOT_FOUND"
    message = "That block is not part of this plan."


class PlanUnavailableError(ServiceUnavailableError):
    code = "PLAN_UNAVAILABLE"
    message = "A plan could not be made right now. Please try again in a moment."


class NothingToPlanFromError(ConflictError):
    code = "INSUFFICIENT_CONTEXT"
    message = (
        "There is not enough known about you to plan your week. "
        "Complete onboarding or add goals to your profile first."
    )
