from datetime import datetime

from app.core.exceptions import ApplicationError
from app.domain.subscription.plans import Feature, Plan


class QuotaExceededError(ApplicationError):
    """The user's plan allows no more of this feature until the period resets.

    Distinct from rate limiting: waiting a few minutes will not help, so a
    client should offer the reset date or an upgrade rather than a retry.
    """

    code = "QUOTA_EXCEEDED"
    message = "You have used this month's allowance for this feature on your plan."

    def __init__(self, plan: Plan, feature: Feature, limit: int, resets_at: datetime) -> None:
        super().__init__()
        self.plan = plan
        self.feature = feature
        self.limit = limit
        self.resets_at = resets_at
