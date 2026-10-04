import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.memory.dto import (
    ExtractionOutcome,
    ExtractionRequest,
    MemoryFilters,
    MemoryPage,
    UpdateMemoryCommand,
)
from app.application.memory.exceptions import MemoryNotFoundError
from app.domain.memory.decisions import DecisionKind, MemoryDecision, reinforced_scores
from app.domain.memory.pairing import ExistingMemory
from app.domain.memory.policy import DURABILITY_LIFETIME_DAYS, MemoryPolicy
from app.domain.memory.types import (
    MemoryCandidate,
    MemoryChange,
    MemorySource,
    MemoryStatus,
)
from app.domain.temporal.ranges import local_midday
from app.graph.memory.graph import MemoryGraph
from app.infrastructure.database.models import Event, Memory, MemoryVersion
from app.infrastructure.database.repositories.event import EventRepository
from app.infrastructure.database.repositories.memory import MemoryRepository
from app.infrastructure.llm.embeddings import EmbeddingProvider
from app.infrastructure.llm.exceptions import LLMError

logger = logging.getLogger(__name__)


class MemoryService:
    def __init__(
        self,
        session: AsyncSession,
        memories: MemoryRepository,
        events: EventRepository,
        graph: MemoryGraph,
        policy: MemoryPolicy,
        embeddings: EmbeddingProvider,
        embedding_model: str,
    ) -> None:
        self._session = session
        self._memories = memories
        self._events = events
        self._graph = graph
        self._policy = policy
        self._embeddings = embeddings
        self._embedding_model = embedding_model

    async def extract_from_exchange(self, request: ExtractionRequest) -> ExtractionOutcome:
        """Decide what one exchange is worth remembering, and record it."""
        now = datetime.now(tz=UTC)

        existing = await self._memories.active_for_comparison(
            request.user_id, limit=self._policy.max_comparisons, now=now
        )
        comparable = [
            ExistingMemory(
                id=str(memory.id),
                content=memory.content,
                memory_type=memory.memory_type,
                importance=memory.importance,
                confidence=memory.confidence,
                embedding=(
                    tuple(memory.embedding)
                    if memory.embedding is not None
                    and memory.embedding_model == self._embedding_model
                    else None
                ),
            )
            for memory in existing
        ]
        by_id = {str(memory.id): memory for memory in existing}

        result: dict[str, Any] = await self._graph.ainvoke(
            {
                "user_id": str(request.user_id),
                "conversation_id": str(request.conversation_id),
                "user_message": request.user_message,
                "assistant_reply": request.assistant_reply,
                "existing": comparable,
                "errors": [],
            }
        )

        outcome = await self._apply(
            decisions=list(result.get("decisions") or []),
            by_id=by_id,
            request=request,
            now=now,
            rejected=int(result.get("rejected_count") or 0),
            judged_pairs=int(result.get("judged_pairs") or 0),
        )

        # New memory and its supersession must become visible together.
        await self._session.commit()

        logger.info(
            "memory_extraction_completed",
            extra={
                "user_id": str(request.user_id),
                "conversation_id": str(request.conversation_id),
                # "created" clashes with a LogRecord attribute, so it's prefixed.
                "memories_created": outcome.created,
                "memories_reinforced": outcome.reinforced,
                "memories_superseded": outcome.superseded,
                "candidates_rejected": outcome.rejected,
                "judged_pairs": outcome.judged_pairs,
            },
        )

        return outcome

    async def list_for_user(
        self, user_id: uuid.UUID, filters: MemoryFilters, *, limit: int, offset: int
    ) -> MemoryPage:
        memories, total = await self._memories.list_for_user(
            user_id,
            limit=limit,
            offset=offset,
            statuses=filters.statuses,
            memory_type=filters.memory_type,
        )
        return MemoryPage(items=memories, total_items=total)

    async def update(
        self, memory_id: uuid.UUID, user_id: uuid.UUID, command: UpdateMemoryCommand
    ) -> Memory:
        memory = await self._require(memory_id, user_id)

        if command.content is not None:
            memory.content = command.content
            # The old vector no longer matches the text, so replace or clear it.
            await self._reembed(memory)
        if command.importance is not None:
            memory.importance = command.importance
        if command.status is not None:
            memory.status = command.status

        self._record_version(memory, MemoryChange.UPDATED, reason="edited by the user")
        await self._session.commit()

        # Refresh the DB-generated updated_at while still in async context.
        await self._session.refresh(memory)

        logger.info("memory_updated", extra={"user_id": str(user_id)})

        return memory

    async def delete(self, memory_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Remove a memory entirely.

        PRD 18 lists DELETED as a lifecycle state, but a user deleting something
        about themselves should have it gone rather than hidden behind a flag,
        and PRD 32 puts that under privacy. ARCHIVED covers "keep it, stop using
        it"; this is the other thing.
        """
        memory = await self._require(memory_id, user_id)
        await self._memories.delete(memory)
        await self._session.commit()

        logger.info("memory_deleted", extra={"user_id": str(user_id)})

    async def _reembed(self, memory: Memory) -> None:
        try:
            vector = await self._embeddings.embed_query(memory.content)
        except LLMError as error:
            logger.warning(
                "memory_reembedding_failed",
                extra={"memory_id": str(memory.id), "error_type": type(error).__name__},
            )
            memory.embedding = None
            memory.embedding_model = None
            return

        memory.embedding = vector
        memory.embedding_model = self._embedding_model

    async def _require(self, memory_id: uuid.UUID, user_id: uuid.UUID) -> Memory:
        memory = await self._memories.get_owned_by(memory_id, user_id)
        if memory is None:
            raise MemoryNotFoundError()
        return memory

    async def _apply(
        self,
        *,
        decisions: list[MemoryDecision],
        by_id: dict[str, Memory],
        request: ExtractionRequest,
        now: datetime,
        rejected: int,
        judged_pairs: int,
    ) -> ExtractionOutcome:
        created = reinforced = superseded = 0

        for decision in decisions:
            if decision.kind is DecisionKind.REINFORCE and decision.reinforces is not None:
                existing = by_id.get(decision.reinforces.id)
                if existing is None:
                    continue
                importance, confidence = reinforced_scores(decision.reinforces, decision.candidate)
                existing.importance = importance
                existing.confidence = confidence
                # The clock restarts: something said again is current again.
                existing.expires_at = _expiry(decision.candidate, now)
                self._record_version(
                    existing, MemoryChange.REINFORCED, reason="stated again by the user"
                )
                reinforced += 1

                # Events are recorded even if the memory is a duplicate: each one counts.
                if decision.candidate.is_countable_event():
                    self._record_event(existing, decision.candidate, request, now)

                continue

            memory = self._create(decision.candidate, request, now, decision.embedding)
            created += 1

            if decision.supersedes or decision.candidate.is_countable_event():
                # Flush so the new row exists before anything references it.
                await self._session.flush()

            if decision.candidate.is_countable_event():
                self._record_event(memory, decision.candidate, request, now)

            for replaced in decision.supersedes:
                previous = by_id.get(replaced.id)
                if previous is None:
                    continue
                previous.status = MemoryStatus.SUPERSEDED
                previous.superseded_by_id = memory.id
                self._record_version(
                    previous, MemoryChange.SUPERSEDED, reason="contradicted by a newer memory"
                )
                superseded += 1

        return ExtractionOutcome(
            created=created,
            reinforced=reinforced,
            superseded=superseded,
            rejected=rejected,
            judged_pairs=judged_pairs,
        )

    def _create(
        self,
        candidate: MemoryCandidate,
        request: ExtractionRequest,
        now: datetime,
        embedding: tuple[float, ...] | None = None,
    ) -> Memory:
        memory = Memory(
            id=uuid.uuid4(),
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            content=candidate.content,
            memory_type=candidate.memory_type,
            status=MemoryStatus.ACTIVE,
            durability=candidate.durability,
            source=MemorySource.CONVERSATION,
            importance=candidate.importance,
            confidence=candidate.confidence,
            is_sensitive=candidate.is_sensitive,
            expires_at=_expiry(candidate, now),
            embedding=list(embedding) if embedding is not None else None,
            embedding_model=self._embedding_model if embedding is not None else None,
        )
        self._memories.add(memory)
        self._record_version(memory, MemoryChange.CREATED, reason="extracted from a conversation")

        return memory

    def _record_event(
        self,
        memory: Memory,
        candidate: MemoryCandidate,
        request: ExtractionRequest,
        now: datetime,
    ) -> None:
        """Also record a countable event alongside the memory.

        Written to its own table rather than left inside the memory text, because
        the question "how many hours last week" has to be answered by summing
        rows, not by a model reading sentences and estimating.
        """
        if candidate.activity is None:
            return

        self._events.add(
            Event(
                user_id=request.user_id,
                conversation_id=request.conversation_id,
                memory_id=memory.id,
                activity=candidate.activity,
                description=candidate.content,
                occurred_at=local_midday(candidate.days_ago or 0, now, request.timezone),
                quantity=candidate.quantity,
                unit=candidate.unit,
            )
        )

    def _record_version(self, memory: Memory, change: MemoryChange, reason: str) -> None:
        self._memories.add_version(
            MemoryVersion(
                memory_id=memory.id,
                change=change,
                content=memory.content,
                status=memory.status,
                importance=memory.importance,
                confidence=memory.confidence,
                reason=reason,
            )
        )


def _expiry(candidate: MemoryCandidate, now: datetime) -> datetime | None:
    lifetime = DURABILITY_LIFETIME_DAYS[candidate.durability]
    return None if lifetime is None else now + timedelta(days=lifetime)
