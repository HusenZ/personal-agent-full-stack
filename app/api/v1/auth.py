from fastapi import APIRouter, status

from app.api.dependencies import AuthServiceDep, CurrentUserDep
from app.api.rate_limiting import (
    DeleteAccountRateLimit,
    LoginAccountRateLimit,
    LoginAddressRateLimit,
    RefreshRateLimit,
    RegisterRateLimit,
)
from app.application.auth.dto import LoginCommand, RegisterUserCommand
from app.core.context import get_request_id
from app.schemas.auth import (
    DeleteAccountRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisteredUserResponse,
    RegisterRequest,
    TokenPairResponse,
)
from app.schemas.common import DataResponse, ResponseMeta

router = APIRouter(prefix="/auth", tags=["auth"])


def _meta() -> ResponseMeta:
    return ResponseMeta(request_id=get_request_id())


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[RegisteredUserResponse],
    summary="Create an account",
)
async def register(
    payload: RegisterRequest, service: AuthServiceDep, _limit: RegisterRateLimit
) -> DataResponse[RegisteredUserResponse]:
    user = await service.register(
        RegisterUserCommand(email=payload.email, password=payload.password)
    )

    return DataResponse(
        data=RegisteredUserResponse(id=user.id, email=user.email, created_at=user.created_at),
        meta=_meta(),
    )


@router.post(
    "/login",
    response_model=DataResponse[TokenPairResponse],
    summary="Exchange credentials for tokens",
)
async def login(
    payload: LoginRequest,
    service: AuthServiceDep,
    _address_limit: LoginAddressRateLimit,
    _account_limit: LoginAccountRateLimit,
) -> DataResponse[TokenPairResponse]:
    tokens = await service.login(LoginCommand(email=payload.email, password=payload.password))

    return DataResponse(
        data=TokenPairResponse(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            access_token_expires_at=tokens.access_token_expires_at,
            refresh_token_expires_at=tokens.refresh_token_expires_at,
        ),
        meta=_meta(),
    )


@router.post(
    "/refresh",
    response_model=DataResponse[TokenPairResponse],
    summary="Rotate a refresh token",
)
async def refresh(
    payload: RefreshRequest, service: AuthServiceDep, _limit: RefreshRateLimit
) -> DataResponse[TokenPairResponse]:
    tokens = await service.refresh(payload.refresh_token)

    return DataResponse(
        data=TokenPairResponse(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            access_token_expires_at=tokens.access_token_expires_at,
            refresh_token_expires_at=tokens.refresh_token_expires_at,
        ),
        meta=_meta(),
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a refresh token and its family",
)
async def logout(payload: LogoutRequest, service: AuthServiceDep) -> None:
    await service.logout(payload.refresh_token)


@router.delete(
    "/account",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete your account and all of its data",
    description="Requires the account password. Irreversible.",
)
async def delete_account(
    payload: DeleteAccountRequest,
    current_user: CurrentUserDep,
    service: AuthServiceDep,
    _limit: DeleteAccountRateLimit,
) -> None:
    await service.delete_account(current_user, payload.password)
