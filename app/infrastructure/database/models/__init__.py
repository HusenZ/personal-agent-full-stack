"""Persistence models.

Every model is imported here so that Alembic's autogenerate sees the complete
metadata; importing only the base would silently produce empty migrations.
"""

from app.infrastructure.database.models.analysis import Analysis
from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.conversation import Conversation, Message
from app.infrastructure.database.models.event import Event
from app.infrastructure.database.models.job import BackgroundJob, JobKind, JobStatus
from app.infrastructure.database.models.memory import Memory, MemoryVersion
from app.infrastructure.database.models.onboarding import OnboardingSession, OnboardingStatus
from app.infrastructure.database.models.plan import WeekPlan
from app.infrastructure.database.models.profile import (
    Constraint,
    Goal,
    GoalPriority,
    Preference,
    Profile,
)
from app.infrastructure.database.models.refresh_token import RefreshToken, RevocationReason
from app.infrastructure.database.models.run import AgentRun, RunKind, RunStatus
from app.infrastructure.database.models.subscription import Subscription, UsageCounter
from app.infrastructure.database.models.user import User

__all__ = [
    "AgentRun",
    "Analysis",
    "BackgroundJob",
    "Base",
    "Constraint",
    "Conversation",
    "Event",
    "Goal",
    "GoalPriority",
    "JobKind",
    "JobStatus",
    "Memory",
    "MemoryVersion",
    "Message",
    "OnboardingSession",
    "OnboardingStatus",
    "Preference",
    "Profile",
    "RefreshToken",
    "RevocationReason",
    "RunKind",
    "RunStatus",
    "Subscription",
    "UsageCounter",
    "User",
    "WeekPlan",
]
