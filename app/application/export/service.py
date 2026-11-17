import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.export.dto import (
    AccountExport,
    ExportedAccount,
    ExportedConversation,
    ExportedMemory,
)
from app.application.export.exceptions import ExportUnavailableError
from app.infrastructure.database.models import User
from app.infrastructure.database.repositories.analysis import AnalysisRepository
from app.infrastructure.database.repositories.conversation import ConversationRepository
from app.infrastructure.database.repositories.event import EventRepository
from app.infrastructure.database.repositories.memory import MemoryRepository
from app.infrastructure.database.repositories.onboarding import OnboardingSessionRepository
from app.infrastructure.database.repositories.plan import WeekPlanRepository
from app.infrastructure.database.repositories.profile import ProfileRepository
from app.infrastructure.database.repositories.subscription import SubscriptionRepository

logger = logging.getLogger(__name__)

# SQLSTATE classes that describe the database's condition rather than the query:
# connection exceptions, transaction rollback, insufficient resources and
# operator intervention (which includes a statement cancelled by a timeout).
_TRANSIENT_SQLSTATE_CLASSES = frozenset({"08", "40", "53", "57"})


class ExportService:
    """Assemble a copy of everything held about one user.

    Every read is scoped to the user in the query itself, so there is no point
    at which another user's rows are loaded and then filtered out.

    The reads run in one read-only, repeatable-read transaction, so the export
    is a single consistent snapshot: a message sent or a memory extracted while
    it runs is either wholly in it or wholly absent. The connection is handed
    back as soon as the reads finish. Turning a large account into JSON and
    sending it takes far longer than reading it, and holding one of a small
    pool's connections for all of that would starve every other request.

    Exports also take a slot from a small per-process pool for as long as the
    caller holds the result, so a burst of them queues instead of exhausting
    the connection pool or memory. A caller waits for a slot holding no
    connection, and is refused with a 503 if none frees up in time.
    """

    def __init__(
        self,
        session: AsyncSession,
        slots: asyncio.Semaphore,
        slot_timeout_seconds: float,
        profiles: ProfileRepository,
        onboarding_sessions: OnboardingSessionRepository,
        analyses: AnalysisRepository,
        conversations: ConversationRepository,
        memories: MemoryRepository,
        events: EventRepository,
        plans: WeekPlanRepository,
        subscriptions: SubscriptionRepository,
    ) -> None:
        self._session = session
        self._slots = slots
        self._slot_timeout_seconds = slot_timeout_seconds
        self._profiles = profiles
        self._onboarding_sessions = onboarding_sessions
        self._analyses = analyses
        self._conversations = conversations
        self._memories = memories
        self._events = events
        self._plans = plans
        self._subscriptions = subscriptions

    @asynccontextmanager
    async def export_for_user(self, user: User) -> AsyncIterator[AccountExport]:
        """Read the user's export; the slot is held until the block exits.

        Hold the block open while the export is being turned into a response,
        since that is when its memory is at its peak.
        """
        # Copied out first: ending authentication's transaction expires the user.
        account = ExportedAccount(id=user.id, email=user.email, created_at=user.created_at)

        # End authentication's transaction, which wrote nothing, so the pooled
        # connection is not held while queueing for a slot below. A transaction
        # can also only choose its isolation before its first statement.
        await self._session.rollback()

        await self._take_slot(account)
        try:
            yield await self._read(account)
        finally:
            self._slots.release()

    async def _take_slot(self, account: ExportedAccount) -> None:
        try:
            async with asyncio.timeout(self._slot_timeout_seconds):
                await self._slots.acquire()
        except TimeoutError:
            logger.warning("account_export_queue_full", extra={"user_id": str(account.id)})
            raise ExportUnavailableError() from None

    async def _read(self, account: ExportedAccount) -> AccountExport:
        try:
            export = await self._read_snapshot(account)
        except Exception as error:
            if not _is_transient_database_failure(error):
                raise
            logger.warning(
                "account_export_unavailable",
                extra={"user_id": str(account.id), "error_type": type(error).__name__},
            )
            raise ExportUnavailableError() from error
        finally:
            # Releases the connection. Loaded rows stay readable, detached.
            await self._session.close()

        # Counts only; the log must not become a second copy of the export.
        logger.info(
            "account_exported",
            extra={
                "user_id": str(account.id),
                "conversations": len(export.conversations),
                "messages": sum(len(item.messages) for item in export.conversations),
                "memories": len(export.memories),
                "events": len(export.events),
            },
        )

        return export

    async def _read_snapshot(self, account: ExportedAccount) -> AccountExport:
        await self._session.connection(
            execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}
        )

        user_id = account.id
        conversations = await self._conversations.all_for_user(user_id)
        messages = _group_by(
            await self._conversations.all_messages_for_user(user_id),
            lambda message: message.conversation_id,
        )
        memories = await self._memories.all_for_user(user_id)
        versions = _group_by(
            await self._memories.all_versions_for_user(user_id),
            lambda version: version.memory_id,
        )

        return AccountExport(
            exported_at=datetime.now(tz=UTC),
            account=account,
            profile=await self._profiles.get_by_user_id(user_id),
            onboarding_sessions=await self._onboarding_sessions.all_for_user(user_id),
            analyses=await self._analyses.all_for_user(user_id),
            conversations=[
                ExportedConversation(
                    conversation=conversation, messages=messages.get(conversation.id, [])
                )
                for conversation in conversations
            ],
            memories=[
                ExportedMemory(memory=memory, versions=versions.get(memory.id, []))
                for memory in memories
            ],
            events=await self._events.all_for_user(user_id),
            week_plans=await self._plans.all_for_user(user_id),
            subscription=await self._subscriptions.get_for_user(user_id),
            usage_counters=await self._subscriptions.all_usage_for_user(user_id),
        )


def _is_transient_database_failure(error: BaseException) -> bool:
    """Whether the database, not the export, is why this failed.

    These are worth a 503 and a retry: a query past its timeout, no pooled
    connection free in time, the server unreachable or the connection dropped,
    or the server shedding load. A fault in the export itself, such as a query
    the database rejects, is not, and stays a 500 so it is noticed and fixed.
    """
    if isinstance(error, TimeoutError | PoolTimeoutError | OSError):
        return True
    if isinstance(error, DBAPIError):
        if error.connection_invalidated or isinstance(error, OperationalError):
            return True
        sqlstate = getattr(error.orig, "sqlstate", None) or ""
        return sqlstate[:2] in _TRANSIENT_SQLSTATE_CLASSES
    return False


def _group_by[RowT](
    rows: list[RowT], parent_id: Callable[[RowT], uuid.UUID]
) -> dict[uuid.UUID, list[RowT]]:
    """Bucket child rows under their parent id, keeping the query's order."""
    grouped: defaultdict[uuid.UUID, list[RowT]] = defaultdict(list)
    for row in rows:
        grouped[parent_id(row)].append(row)
    return grouped
