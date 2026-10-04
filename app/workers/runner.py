import asyncio
import contextlib
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.memory.background import MemoryExtractionHandler
from app.core.config import Settings
from app.domain.memory.policy import DEFAULT_MEMORY_POLICY
from app.graph.memory.graph import MemoryGraph
from app.infrastructure.database.models import BackgroundJob, JobKind
from app.infrastructure.database.repositories.job import JobRepository
from app.infrastructure.database.repositories.retention import RetentionRepository
from app.infrastructure.llm.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)

# However many failures, a retry is never put off longer than this.
MAX_RETRY_DELAY = timedelta(hours=1)

# Pruning is rate-limited and batched so it never crowds out jobs.
PRUNE_INTERVAL_SECONDS = 3600.0
PRUNE_BATCH = 1000
PRUNE_MAX_BATCHES = 10


class JobHandler(Protocol):
    async def __call__(self, session: AsyncSession, job: BackgroundJob, *, now: datetime) -> bool:
        """Do the job's work and mark it done in one transaction.

        Returns False when the claim was superseded and nothing was committed.
        """
        ...


@dataclass(frozen=True)
class JobWorker:
    """Claims due jobs one at a time and runs them, until told to stop.

    One job at a time per worker keeps database connections and provider calls
    bounded; more throughput comes from running more worker processes, which
    the claim query is built to allow.
    """

    session_factory: async_sessionmaker[AsyncSession]
    handlers: Mapping[JobKind, JobHandler]
    poll_interval: float
    timeout: float
    lease: timedelta
    retry_base: timedelta
    run_retention: timedelta = timedelta(days=30)
    job_retention: timedelta = timedelta(days=7)

    async def run(self, stop: asyncio.Event) -> None:
        logger.info("worker_started", extra={"kinds": sorted(kind.value for kind in self.handlers)})
        next_prune = time.monotonic()
        while not stop.is_set():
            if time.monotonic() >= next_prune:
                next_prune = time.monotonic() + PRUNE_INTERVAL_SECONDS
                try:
                    await self.prune()
                except Exception:
                    # Housekeeping only; retried next interval and never blocks jobs.
                    logger.exception("retention_prune_failed")

            try:
                processed = await self.run_once()
            except Exception:
                # Likely the database is down; keep looping so the worker recovers.
                logger.exception("worker_poll_failed")
                processed = False

            if not processed:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
        logger.info("worker_stopped")

    async def run_once(self) -> bool:
        """Claim and run one due job. False when there was none."""
        async with self.session_factory() as session:
            job = await JobRepository(session).claim(now=_now(), lease=self.lease)
            await session.commit()

        if job is None:
            return False

        await self._process(job)
        return True

    async def _process(self, job: BackgroundJob) -> None:
        fields = {"job_id": str(job.id), "job_kind": job.kind.value, "attempt": job.attempts}
        handler = self.handlers.get(job.kind)
        started = time.perf_counter()

        try:
            if handler is None:
                raise LookupError(f"no handler for {job.kind.value}")
            # Timeout stays below the lease so running jobs aren't reclaimed.
            async with asyncio.timeout(self.timeout):
                async with self.session_factory() as session:
                    completed = await handler(session, job, now=_now())
        except Exception as error:
            await self._record_failure(job, error, fields)
            return

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        if completed:
            logger.info("job_succeeded", extra={**fields, "duration_ms": duration_ms})
        else:
            logger.warning("job_claim_superseded", extra=fields)

    async def _record_failure(
        self, job: BackgroundJob, error: Exception, fields: dict[str, object]
    ) -> None:
        now = _now()
        will_retry = job.attempts < job.max_attempts
        retry_at = now + self.backoff(job.attempts) if will_retry else None

        logger.warning(
            "job_failed",
            exc_info=error,
            extra={
                **fields,
                "error_type": type(error).__name__,
                "will_retry": will_retry,
                "max_attempts": job.max_attempts,
            },
        )

        if job.claim_token is None:
            return
        try:
            async with self.session_factory() as session:
                await JobRepository(session).mark_failed(
                    job.id,
                    job.claim_token,
                    error_type=type(error).__name__,
                    retry_at=retry_at,
                    now=now,
                )
                await session.commit()
        except Exception:
            # Left claimed; retried once the lease expires, so nothing is lost.
            logger.exception("job_failure_not_recorded", extra=fields)

    async def prune(self) -> dict[str, int]:
        """Delete run records and finished jobs older than their retention."""
        now = _now()
        removed = {"runs": 0, "jobs": 0}
        for _ in range(PRUNE_MAX_BATCHES):
            async with self.session_factory() as session:
                repository = RetentionRepository(session)
                runs = await repository.delete_runs_before(
                    now - self.run_retention, limit=PRUNE_BATCH
                )
                jobs = await repository.delete_finished_jobs_before(
                    now - self.job_retention, limit=PRUNE_BATCH
                )
                await session.commit()
            removed["runs"] += runs
            removed["jobs"] += jobs
            if runs < PRUNE_BATCH and jobs < PRUNE_BATCH:
                break
        if removed["runs"] or removed["jobs"]:
            logger.info("retention_pruned", extra=removed)
        return removed

    def backoff(self, attempts: int) -> timedelta:
        return min(self.retry_base * (1 << max(0, attempts - 1)), MAX_RETRY_DELAY)


def build_worker(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    memory_graph: MemoryGraph,
    embeddings: EmbeddingProvider,
) -> JobWorker:
    return JobWorker(
        session_factory=session_factory,
        handlers={
            JobKind.MEMORY_EXTRACTION: MemoryExtractionHandler(
                graph=memory_graph,
                policy=DEFAULT_MEMORY_POLICY,
                embeddings=embeddings,
                embedding_model=settings.embedding_model,
            )
        },
        poll_interval=settings.worker_poll_interval_seconds,
        timeout=settings.job_timeout_seconds,
        lease=timedelta(seconds=settings.job_lease_seconds),
        retry_base=timedelta(seconds=settings.job_retry_base_seconds),
        run_retention=timedelta(days=settings.run_retention_days),
        job_retention=timedelta(days=settings.job_retention_days),
    )


def _now() -> datetime:
    return datetime.now(tz=UTC)
