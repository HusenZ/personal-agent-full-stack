from datetime import date, datetime

from pydantic import BaseModel, Field

from app.domain.subscription.plans import Feature, Plan


class FeatureUsageResponse(BaseModel):
    feature: Feature
    used: int
    limit: int
    remaining: int


class SubscriptionResponse(BaseModel):
    plan: Plan
    expires_at: datetime | None = Field(description="When the plan ends, or null if it does not.")
    period_starts_on: date = Field(description="First day of the current usage period, in UTC.")
    resets_at: datetime = Field(description="When every allowance below starts again from zero.")
    usage: list[FeatureUsageResponse]
