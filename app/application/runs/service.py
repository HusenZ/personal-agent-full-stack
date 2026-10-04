import uuid

from app.application.runs.exceptions import RunNotFoundError
from app.infrastructure.database.models import AgentRun
from app.infrastructure.database.repositories.run import RunRepository


class RunService:
    def __init__(self, runs: RunRepository) -> None:
        self._runs = runs

    async def get_run(self, run_id: uuid.UUID, user_id: uuid.UUID) -> AgentRun:
        run = await self._runs.get_owned_by(run_id, user_id)
        if run is None:
            raise RunNotFoundError()
        return run
