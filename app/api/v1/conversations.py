import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.dependencies import ConversationServiceDep, CurrentUserDep
from app.api.v1.chat import to_message_response
from app.core.context import get_request_id
from app.schemas.conversation import (
    ConversationDetailResponse,
    ConversationSummaryResponse,
)
from app.schemas.pagination import (
    PageMeta,
    PageParams,
    PaginatedItemResponse,
    PaginatedResponse,
    page_params,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])

PageParamsDep = Annotated[PageParams, Depends(page_params)]


@router.get(
    "",
    response_model=PaginatedResponse[ConversationSummaryResponse],
    summary="List your conversations, most recent first",
)
async def list_conversations(
    current_user: CurrentUserDep, service: ConversationServiceDep, params: PageParamsDep
) -> PaginatedResponse[ConversationSummaryResponse]:
    page = await service.list_conversations(
        current_user.id, limit=params.page_size, offset=params.offset
    )

    return PaginatedResponse(
        data=[
            ConversationSummaryResponse(
                id=conversation.id,
                title=conversation.title,
                message_count=conversation.message_count,
                created_at=conversation.created_at,
                last_message_at=conversation.last_message_at,
            )
            for conversation in page.items
        ],
        meta=PageMeta.build(params, page.total_items, get_request_id()),
    )


@router.get(
    "/{conversation_id}",
    response_model=PaginatedItemResponse[ConversationDetailResponse],
    summary="Read one conversation and a page of its messages",
)
async def read_conversation(
    conversation_id: uuid.UUID,
    current_user: CurrentUserDep,
    service: ConversationServiceDep,
    params: PageParamsDep,
) -> PaginatedItemResponse[ConversationDetailResponse]:
    detail = await service.get_conversation(
        conversation_id, current_user.id, limit=params.page_size, offset=params.offset
    )

    return PaginatedItemResponse(
        data=ConversationDetailResponse(
            id=detail.conversation.id,
            title=detail.conversation.title,
            created_at=detail.conversation.created_at,
            last_message_at=detail.conversation.last_message_at,
            messages=[to_message_response(message) for message in detail.messages],
        ),
        meta=PageMeta.build(params, detail.total_messages, get_request_id()),
    )


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation and its messages",
    description="Memories learned from the conversation are kept; manage them under /memories.",
)
async def delete_conversation(
    conversation_id: uuid.UUID, current_user: CurrentUserDep, service: ConversationServiceDep
) -> None:
    await service.delete_conversation(conversation_id, current_user.id)
