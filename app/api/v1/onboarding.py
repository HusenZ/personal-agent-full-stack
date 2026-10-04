from typing import Literal, cast

from fastapi import APIRouter, status

from app.api.dependencies import CurrentUserDep, OnboardingServiceDep
from app.api.rate_limiting import OnboardingMessageRateLimit
from app.application.onboarding.dto import OnboardingTurn
from app.core.context import get_request_id
from app.schemas.common import DataResponse, ResponseMeta
from app.schemas.onboarding import (
    OnboardingQuestionResponse,
    OnboardingTurnResponse,
    SubmitAnswerRequest,
)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

StatusLiteral = Literal["in_progress", "completed", "abandoned"]


def _to_response(turn: OnboardingTurn) -> DataResponse[OnboardingTurnResponse]:
    return DataResponse(
        data=OnboardingTurnResponse(
            session_id=turn.session_id,
            status=cast(StatusLiteral, turn.status),
            is_complete=turn.is_complete,
            questions_asked=turn.questions_asked,
            overall_confidence=round(turn.overall_confidence, 4),
            coverage={key: round(value, 4) for key, value in turn.coverage.items()},
            question=(
                OnboardingQuestionResponse(
                    text=turn.question.text, dimension=turn.question.dimension
                )
                if turn.question is not None
                else None
            ),
        ),
        meta=ResponseMeta(request_id=get_request_id()),
    )


@router.post(
    "/start",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[OnboardingTurnResponse],
    summary="Begin or resume onboarding",
)
async def start(
    current_user: CurrentUserDep, service: OnboardingServiceDep
) -> DataResponse[OnboardingTurnResponse]:
    return _to_response(await service.start(current_user.id))


@router.post(
    "/message",
    response_model=DataResponse[OnboardingTurnResponse],
    summary="Answer the current question",
)
async def submit_answer(
    payload: SubmitAnswerRequest,
    current_user: CurrentUserDep,
    service: OnboardingServiceDep,
    _limit: OnboardingMessageRateLimit,
) -> DataResponse[OnboardingTurnResponse]:
    return _to_response(await service.submit_answer(current_user.id, payload.answer))


@router.get(
    "/status",
    response_model=DataResponse[OnboardingTurnResponse],
    summary="Read onboarding progress",
)
async def read_status(
    current_user: CurrentUserDep, service: OnboardingServiceDep
) -> DataResponse[OnboardingTurnResponse]:
    return _to_response(await service.get_status(current_user.id))
