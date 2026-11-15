import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.database.repositories.memory import MemoryRepository, StaleEmbedding
from app.infrastructure.llm.embeddings import EmbeddingProvider
from app.infrastructure.llm.exceptions import (
    LLMConfigurationError,
    LLMError,
    LLMInvalidOutputError,
)

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 50


@dataclass(frozen=True)
class BackfillReport:
    # Given a vector by this run.
    embedded: int = 0
    # Left exactly as they were because the provider failed on them.
    failed: int = 0
    # Edited, or embedded by someone else, between being read and written.
    changed: int = 0
    batches: int = 0


class EmbeddingBackfill:
    """Embeds memories that semantic search cannot currently use.

    Search only compares vectors from the configured model, so a memory whose
    embedding failed when it was saved, or was made by a previous model, is
    found by its words alone. This walks those memories once, in batches, and
    gives each a current vector.

    Safe to run any number of times, including alongside the API: only stale
    rows are read, and a vector is written only if the row is still stale and
    still holds the text that was embedded. A run with nothing to do makes no
    provider calls.

    By default only memories search could return are embedded: active and
    unexpired. ``include_inactive`` adds archived and superseded ones.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        embeddings: EmbeddingProvider,
        embedding_model: str,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        include_inactive: bool = False,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        # One short session per step, so no connection is held during model calls.
        self._sessions = sessions
        self._embeddings = embeddings
        self._embedding_model = embedding_model
        self._batch_size = batch_size
        self._include_inactive = include_inactive

    async def pending(self) -> int:
        """How many memories a run would try to embed now."""
        async with self._sessions() as session:
            return await MemoryRepository(session).count_stale_embeddings(
                self._embedding_model, now=_now(), include_inactive=self._include_inactive
            )

    async def run(self) -> BackfillReport:
        """Embed every stale memory once.

        A memory the provider fails on is left as it was and the run carries on;
        the next run tries it again. Rejected credentials or a missing key end
        the run instead, since every remaining call would fail the same way.
        Each batch commits on its own, so a run that stops partway keeps what
        it finished.
        """
        embedded = failed = changed = batches = 0
        after: uuid.UUID | None = None

        while True:
            async with self._sessions() as session:
                batch = await MemoryRepository(session).stale_embeddings(
                    self._embedding_model,
                    after=after,
                    limit=self._batch_size,
                    now=_now(),
                    include_inactive=self._include_inactive,
                )
            if not batch:
                break

            # Past this batch whatever happens to it, so failures aren't retried this run.
            after = batch[-1].id
            batches += 1

            vectors = await self._embed(batch)
            ready = [(memory, vector) for memory, vector in vectors if vector is not None]
            failed += len(vectors) - len(ready)

            written = await self._store(ready)
            embedded += written
            changed += len(ready) - written

            logger.info(
                "embedding_backfill_batch",
                extra={
                    "batch_size": len(batch),
                    "embedded": written,
                    "failed": len(vectors) - len(ready),
                    "embedding_model": self._embedding_model,
                },
            )

        report = BackfillReport(embedded=embedded, failed=failed, changed=changed, batches=batches)
        logger.info(
            "embedding_backfill_completed",
            extra={
                "embedded": report.embedded,
                "failed": report.failed,
                "changed": report.changed,
                "batches": report.batches,
                "embedding_model": self._embedding_model,
            },
        )
        return report

    async def _embed(
        self, batch: list[StaleEmbedding]
    ) -> list[tuple[StaleEmbedding, list[float] | None]]:
        """A vector for each memory, or None where the provider failed on it."""
        try:
            vectors = await self._embeddings.embed_documents([memory.content for memory in batch])
        except LLMConfigurationError:
            raise
        except LLMInvalidOutputError as error:
            # Possibly one input spoiling the batch; retry singly to find it.
            logger.warning(
                "embedding_backfill_batch_rejected",
                extra={"batch_size": len(batch), "error_type": type(error).__name__},
            )
            return [(memory, await self._embed_one(memory)) for memory in batch]
        except LLMError as error:
            # Unavailable or rate limited: about the provider, not these inputs,
            # and the provider has already retried. Retrying singly would only
            # multiply the calls into an outage.
            self._log_failure(batch, error)
            return [(memory, None) for memory in batch]

        return list(zip(batch, vectors, strict=True))

    async def _embed_one(self, memory: StaleEmbedding) -> list[float] | None:
        try:
            return (await self._embeddings.embed_documents([memory.content]))[0]
        except LLMConfigurationError:
            raise
        except LLMError as error:
            self._log_failure([memory], error)
            return None

    def _log_failure(self, memories: list[StaleEmbedding], error: LLMError) -> None:
        for memory in memories:
            logger.warning(
                "embedding_backfill_failed",
                extra={"memory_id": str(memory.id), "error_type": type(error).__name__},
            )

    async def _store(self, ready: list[tuple[StaleEmbedding, list[float]]]) -> int:
        if not ready:
            return 0
        async with self._sessions() as session:
            repository = MemoryRepository(session)
            written = 0
            for memory, vector in ready:
                if await repository.store_embedding(memory, vector, self._embedding_model):
                    written += 1
            await session.commit()
        return written


def _now() -> datetime:
    return datetime.now(tz=UTC)
