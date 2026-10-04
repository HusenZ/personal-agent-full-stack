import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import BackgroundJob, JobStatus


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, job: BackgroundJob) -> None:
        self._session.add(job)

    async def claim(self, *, now: datetime, lease: timedelta) -> BackgroundJob | None:
        """Take the oldest due job, or one whose previous claim has lapsed.

        SKIP LOCKED lets any number of workers poll at once without blocking on
        each other or taking the same job. A job being worked on is row-locked
        for the whole of its processing transaction, so it is skipped here even
        if its lease has run out; only a worker that died, and whose connection
        and locks died with it, leaves a job claimable again.
        """
        due = (
            select(BackgroundJob.id)
            .where(
                or_(
                    and_(BackgroundJob.status == JobStatus.PENDING, BackgroundJob.run_after <= now),
                    and_(
                        BackgroundJob.status == JobStatus.RUNNING,
                        BackgroundJob.locked_until < now,
                    ),
                )
            )
            .order_by(BackgroundJob.run_after)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        statement = (
            update(BackgroundJob)
            .where(BackgroundJob.id == due)
            .values(
                status=JobStatus.RUNNING,
                attempts=BackgroundJob.attempts + 1,
                locked_until=now + lease,
                claim_token=uuid.uuid4(),
            )
            .returning(BackgroundJob)
            .execution_options(synchronize_session=False)
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def mark_succeeded(
        self, job_id: uuid.UUID, claim_token: uuid.UUID, now: datetime
    ) -> bool:
        """Complete the job if this worker's claim still stands.

        Meant to run first inside the transaction that does the job's work:
        the update row-locks the job until that transaction ends, and the work
        and its completion then commit together or not at all. False means the
        claim was superseded and the work must not be committed.
        """
        statement = (
            update(BackgroundJob)
            .where(
                BackgroundJob.id == job_id,
                BackgroundJob.claim_token == claim_token,
                BackgroundJob.status == JobStatus.RUNNING,
            )
            .values(status=JobStatus.SUCCEEDED, finished_at=now, locked_until=None)
            .returning(BackgroundJob.id)
            .execution_options(synchronize_session=False)
        )
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def mark_failed(
        self,
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
        *,
        error_type: str,
        retry_at: datetime | None,
        now: datetime,
    ) -> None:
        """Schedule another attempt, or give up when retry_at is None."""
        values: dict[str, object] = {
            "last_error_type": error_type,
            "locked_until": None,
            "claim_token": None,
        }
        if retry_at is None:
            values |= {"status": JobStatus.FAILED, "finished_at": now}
        else:
            values |= {"status": JobStatus.PENDING, "run_after": retry_at}

        await self._session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job_id, BackgroundJob.claim_token == claim_token)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
