import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.memory.dto import ExtractionRequest
from app.application.memory.service import MemoryService
from app.domain.memory.policy import MemoryPolicy
from app.graph.memory.graph import MemoryGraph
from app.infrastructure.database.models import BackgroundJob, JobKind
from app.infrastructure.database.repositories.conversation import ConversationRepository
from app.infrastructure.database.repositories.event import EventRepository
from app.infrastructure.database.repositories.job import JobRepository
from app.infrastructure.database.repositories.memory import MemoryRepository
from app.infrastructure.llm.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryExtractionPayload:
    """Which exchange to learn from, by reference.

    The messages are read from the database when the job runs, so the queue
    holds no copy of what was said.
    """

    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    reply_message_id: uuid.UUID

    # Keep unverified replies out of extraction so they never become evidence.
    include_reply: bool

    # Places events on the right day in the user's calendar, not the server's.
    timezone: str

    def to_json(self) -> dict[str, Any]:
        return {
            "conversation_id": str(self.conversation_id),
            "user_message_id": str(self.user_message_id),
            "reply_message_id": str(self.reply_message_id),
            "include_reply": self.include_reply,
            "timezone": self.timezone,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "MemoryExtractionPayload":
        return cls(
            conversation_id=uuid.UUID(data["conversation_id"]),
            user_message_id=uuid.UUID(data["user_message_id"]),
            reply_message_id=uuid.UUID(data["reply_message_id"]),
            include_reply=bool(data["include_reply"]),
            timezone=str(data["timezone"]),
        )


def memory_extraction_job(
    user_id: uuid.UUID, payload: MemoryExtractionPayload, *, max_attempts: int
) -> BackgroundJob:
    return BackgroundJob(
        user_id=user_id,
        kind=JobKind.MEMORY_EXTRACTION,
        payload=payload.to_json(),
        max_attempts=max_attempts,
    )


@dataclass(frozen=True)
class MemoryExtractionHandler:
    """Learns from one stored exchange, exactly once.

    The job is marked done inside the same transaction as the memories it
    writes. A retry after a crash therefore either finds the work committed and
    the job finished, or finds neither; it can never record an event twice.
    """

    graph: MemoryGraph
    policy: MemoryPolicy
    embeddings: EmbeddingProvider
    embedding_model: str

    async def __call__(self, session: AsyncSession, job: BackgroundJob, *, now: datetime) -> bool:
        if job.claim_token is None or not await JobRepository(session).mark_succeeded(
            job.id, job.claim_token, now
        ):
            await session.rollback()
            return False

        payload = MemoryExtractionPayload.from_json(job.payload)
        messages = await ConversationRepository(session).messages_owned_by(
            [payload.user_message_id, payload.reply_message_id], job.user_id
        )
        user_message = messages.get(payload.user_message_id)
        reply = messages.get(payload.reply_message_id)

        if user_message is None or reply is None:
            # Deleted since queued; nothing left to do, so not a failure.
            logger.info("memory_extraction_source_gone", extra={"job_id": str(job.id)})
            await session.commit()
            return True

        service = MemoryService(
            session=session,
            memories=MemoryRepository(session),
            events=EventRepository(session),
            graph=self.graph,
            policy=self.policy,
            embeddings=self.embeddings,
            embedding_model=self.embedding_model,
        )
        # Commits once, carrying the job's completion with it.
        await service.extract_from_exchange(
            ExtractionRequest(
                user_id=job.user_id,
                conversation_id=payload.conversation_id,
                user_message=user_message.content,
                assistant_reply=reply.content if payload.include_reply else "",
                timezone=payload.timezone,
            )
        )
        return True
