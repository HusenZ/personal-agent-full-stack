from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import AgentRun, BackgroundJob, JobStatus


class RetentionRepository:
    """Deletes operational records past their retention, a bounded batch at a time.

    Batched so a large backlog is cleared in many short transactions rather than
    one long one that would lock the table against the work writing to it.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def delete_runs_before(self, cutoff: datetime, *, limit: int) -> int:
        batch = select(AgentRun.id).where(AgentRun.started_at < cutoff).limit(limit)
        result = await self._session.execute(
            delete(AgentRun).where(AgentRun.id.in_(batch)).returning(AgentRun.id)
        )
        return len(result.all())

    async def delete_finished_jobs_before(self, cutoff: datetime, *, limit: int) -> int:
        batch = (
            select(BackgroundJob.id)
            .where(
                BackgroundJob.status.in_((JobStatus.SUCCEEDED, JobStatus.FAILED)),
                BackgroundJob.finished_at < cutoff,
            )
            .limit(limit)
        )
        result = await self._session.execute(
            delete(BackgroundJob).where(BackgroundJob.id.in_(batch)).returning(BackgroundJob.id)
        )
        return len(result.all())
