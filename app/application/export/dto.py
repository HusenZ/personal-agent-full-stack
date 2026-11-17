import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app.infrastructure.database.models import (
    Analysis,
    Conversation,
    Event,
    Memory,
    OnboardingSession,
    Profile,
    Subscription,
    UsageCounter,
    WeekPlan,
)
from app.infrastructure.database.repositories.conversation import MessageRow
from app.infrastructure.database.repositories.memory import MemoryVersionRow


@dataclass(frozen=True)
class ExportedAccount:
    """The account row, reduced to what belongs in the user's hands.

    A value object rather than the ``User`` model on purpose: the model carries
    the password hash, and an export must not be one forgotten field away from
    including it.
    """

    id: uuid.UUID
    email: str
    created_at: datetime


@dataclass(frozen=True)
class ExportedConversation:
    conversation: Conversation
    messages: list[MessageRow] = field(default_factory=list)


@dataclass(frozen=True)
class ExportedMemory:
    memory: Memory
    versions: list[MemoryVersionRow] = field(default_factory=list)


@dataclass(frozen=True)
class AccountExport:
    """Everything the system holds about one user, read in one request.

    Refresh tokens are deliberately absent: they are credentials, not data about
    the user, and a copy of them outside the database is a copy that can be
    stolen.
    """

    exported_at: datetime
    account: ExportedAccount
    profile: Profile | None
    onboarding_sessions: list[OnboardingSession] = field(default_factory=list)
    analyses: list[Analysis] = field(default_factory=list)
    conversations: list[ExportedConversation] = field(default_factory=list)
    memories: list[ExportedMemory] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    week_plans: list[WeekPlan] = field(default_factory=list)
    subscription: Subscription | None = None
    usage_counters: list[UsageCounter] = field(default_factory=list)
