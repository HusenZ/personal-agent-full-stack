from fastapi import APIRouter

from app.api.dependencies import CurrentUserDep, ProfileServiceDep
from app.application.profile.dto import GoalInput, UpdateProfileCommand
from app.core.context import get_request_id
from app.infrastructure.database.models import Profile
from app.schemas.common import DataResponse, ResponseMeta
from app.schemas.profile import GoalResponse, ProfileResponse, UpdateProfileRequest

router = APIRouter(prefix="/profile", tags=["profile"])


def _to_response(profile: Profile) -> DataResponse[ProfileResponse]:
    return DataResponse(
        data=ProfileResponse(
            timezone=profile.timezone,
            goals=[GoalResponse(name=goal.name, priority=goal.priority) for goal in profile.goals],
            constraints=[constraint.description for constraint in profile.constraints],
            preferences={preference.key: preference.value for preference in profile.preferences},
        ),
        meta=ResponseMeta(request_id=get_request_id()),
    )


@router.get("", response_model=DataResponse[ProfileResponse], summary="Read your profile")
async def read_profile(
    current_user: CurrentUserDep, service: ProfileServiceDep
) -> DataResponse[ProfileResponse]:
    return _to_response(await service.get_for_user(current_user.id))


@router.patch("", response_model=DataResponse[ProfileResponse], summary="Update your profile")
async def update_profile(
    payload: UpdateProfileRequest, current_user: CurrentUserDep, service: ProfileServiceDep
) -> DataResponse[ProfileResponse]:
    command = UpdateProfileCommand(
        timezone=payload.timezone,
        goals=(
            [GoalInput(name=goal.name, priority=goal.priority) for goal in payload.goals]
            if payload.goals is not None
            else None
        ),
        constraints=payload.constraints,
        preferences=payload.preferences,
    )

    # Always use the authenticated user's id, never one from the request.
    return _to_response(await service.update_for_user(current_user.id, command))
