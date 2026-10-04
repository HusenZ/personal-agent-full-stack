import uuid

from fastapi import APIRouter

from app.api.dependencies import CurrentUserDep, RunServiceDep
from app.core.context import get_request_id
from app.infrastructure.database.models import AgentRun
from app.schemas.common import DataResponse, ResponseMeta
from app.schemas.run import RunResponse, RunTraceResponse, StepResponse

router = APIRouter(prefix="/runs", tags=["runs"])


def to_run_response(run: AgentRun) -> RunResponse:
    return RunResponse.model_validate(run, from_attributes=True)


@router.get(
    "/{run_id}",
    response_model=DataResponse[RunResponse],
    summary="Read the summary of one of your agent runs",
)
async def read_run(
    run_id: uuid.UUID, current_user: CurrentUserDep, service: RunServiceDep
) -> DataResponse[RunResponse]:
    run = await service.get_run(run_id, current_user.id)
    return DataResponse(data=to_run_response(run), meta=ResponseMeta(request_id=get_request_id()))


@router.get(
    "/{run_id}/trace",
    response_model=DataResponse[RunTraceResponse],
    summary="Read the step-by-step trace of one of your agent runs",
)
async def read_run_trace(
    run_id: uuid.UUID, current_user: CurrentUserDep, service: RunServiceDep
) -> DataResponse[RunTraceResponse]:
    run = await service.get_run(run_id, current_user.id)
    return DataResponse(
        data=RunTraceResponse(
            run=to_run_response(run),
            steps=[StepResponse.model_validate(step) for step in run.steps],
        ),
        meta=ResponseMeta(request_id=get_request_id()),
    )
