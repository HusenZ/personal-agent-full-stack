import asyncio

from fastapi import APIRouter, Response

from app.api.dependencies import CurrentUserDep, ExportServiceDep
from app.api.rate_limiting import ExportRateLimit
from app.api.v1.memories import to_memory_response
from app.application.export.dto import AccountExport
from app.core.context import get_request_id
from app.schemas.common import DataResponse, ResponseMeta
from app.schemas.conversation import MessageResponse
from app.schemas.export import (
    AccountExportResponse,
    ExportedAccountResponse,
    ExportedAnalysisResponse,
    ExportedConversationResponse,
    ExportedEventResponse,
    ExportedMemoryResponse,
    ExportedMemoryVersionResponse,
    ExportedOnboardingSessionResponse,
    ExportedProfileResponse,
    ExportedSubscriptionResponse,
    ExportedUsageCounterResponse,
    ExportedWeekPlanResponse,
)
from app.schemas.profile import GoalResponse

router = APIRouter(prefix="/account", tags=["account"])


def _to_response(export: AccountExport) -> AccountExportResponse:
    profile = export.profile
    return AccountExportResponse(
        exported_at=export.exported_at,
        account=ExportedAccountResponse(
            id=export.account.id, email=export.account.email, created_at=export.account.created_at
        ),
        profile=(
            ExportedProfileResponse(
                timezone=profile.timezone,
                goals=[
                    GoalResponse(name=goal.name, priority=goal.priority) for goal in profile.goals
                ],
                constraints=[constraint.description for constraint in profile.constraints],
                preferences={item.key: item.value for item in profile.preferences},
                created_at=profile.created_at,
                updated_at=profile.updated_at,
            )
            if profile is not None
            else None
        ),
        onboarding_sessions=[
            ExportedOnboardingSessionResponse.model_validate(item, from_attributes=True)
            for item in export.onboarding_sessions
        ],
        analyses=[
            ExportedAnalysisResponse.model_validate(item, from_attributes=True)
            for item in export.analyses
        ],
        conversations=[
            ExportedConversationResponse(
                id=item.conversation.id,
                title=item.conversation.title,
                message_count=item.conversation.message_count,
                created_at=item.conversation.created_at,
                last_message_at=item.conversation.last_message_at,
                messages=[
                    MessageResponse(
                        id=message.id,
                        role=message.role,
                        content=message.content,
                        created_at=message.created_at,
                    )
                    for message in item.messages
                ],
            )
            for item in export.conversations
        ],
        memories=[
            ExportedMemoryResponse(
                **to_memory_response(item.memory).model_dump(),
                conversation_id=item.memory.conversation_id,
                versions=[
                    ExportedMemoryVersionResponse.model_validate(version, from_attributes=True)
                    for version in item.versions
                ],
            )
            for item in export.memories
        ],
        events=[
            ExportedEventResponse.model_validate(item, from_attributes=True)
            for item in export.events
        ],
        week_plans=[
            ExportedWeekPlanResponse.model_validate(item, from_attributes=True)
            for item in export.week_plans
        ],
        subscription=(
            ExportedSubscriptionResponse.model_validate(export.subscription, from_attributes=True)
            if export.subscription is not None
            else None
        ),
        usage_counters=[
            ExportedUsageCounterResponse.model_validate(item, from_attributes=True)
            for item in export.usage_counters
        ],
    )


@router.get(
    "/export",
    response_model=DataResponse[AccountExportResponse],
    summary="Download a copy of everything held about you",
    description=(
        "One JSON document with your account, profile, onboarding interviews, analyses, "
        "conversations and their messages, memories with their change history, events, "
        "week plans, and subscription with its usage counters. It never includes "
        "credentials: no password hash and no refresh tokens. Memory embedding vectors are "
        "omitted as they are derived from the memory text. Answers 429 when requested too "
        "often and 503 EXPORT_UNAVAILABLE when it cannot be prepared right now; both are "
        "safe to retry later."
    ),
)
async def export_account(
    current_user: CurrentUserDep, service: ExportServiceDep, _limit: ExportRateLimit
) -> Response:
    async with service.export_for_user(current_user) as export:
        # Building and encoding a large account's document is seconds of CPU.
        # On the event loop that would stall every other request this worker
        # is serving, so it runs on a thread and the encoded bytes are returned
        # as they are. The response_model above still documents the shape.
        body = await asyncio.to_thread(_render, export, get_request_id())

    # Named as a file so a browser saves it rather than rendering it.
    filename = f"personal-intelligence-export-{export.exported_at:%Y-%m-%d}.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _render(export: AccountExport, request_id: str | None) -> bytes:
    envelope = DataResponse[AccountExportResponse](
        data=_to_response(export), meta=ResponseMeta(request_id=request_id)
    )
    return envelope.model_dump_json().encode()
